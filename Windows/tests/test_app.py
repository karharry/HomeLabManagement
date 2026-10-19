import hashlib
import time
from types import SimpleNamespace

import pytest

import docker_
import history
import tls_pin
import update_check
import wireguard_
import wol
from core import Fmt, Machine, MachineKind, NavItem, store


def test_docker_parse_joins_stats_and_compose_project():
    text = (
        '{"ID":"abc","Names":"web","Image":"nginx","State":"running","Status":"Up 1h","Ports":"80/tcp",'
        '"Labels":"com.docker.compose.project=ix-plex,a=b"}\n---STATS---\n'
        '{"Name":"web","CPUPerc":"1.5%","MemUsage":"10MiB / 2GiB"}\n')
    [c] = docker_.parse(text)
    assert c.is_running and c.cpu == "1.5%" and c.memory == "10MiB" and c.truenas_app == "plex"


def test_wireguard_up_and_down_tunnels():
    dump = "wg0\tPRIV\tPUB\t51820\toff\nwg0\tPEER\t(none)\t1.2.3.4:51820\t10.0.0.0/24\t0\t5\t7\t25\n"
    wg0, wg1 = wireguard_.tunnels(["wg0", "wg1"], dump)
    assert wg0.is_up and wg0.rx_bytes == 5 and wg0.latest_handshake == "Never"
    assert not wg1.is_up
    assert not wireguard_.is_valid_name("bad name; rm")


def test_fmt():
    assert Fmt.percent(None) == "–" and Fmt.percent(41.6) == "42%"


def test_every_kind_gets_history_page():
    assert all(NavItem.history in k.nav_items for k in MachineKind)


def test_web_url_derivation():
    tn = Machine(kind=MachineKind.truenas, host="10.0.0.2", port=443, useTLS=True)
    assert tn.web_url == "https://10.0.0.2:443"
    assert Machine(kind=MachineKind.debian, host="h").web_url == ""
    assert Machine(kind=MachineKind.debian, host="h", webUrl="http://x").web_url == "http://x"


def test_wol_packet():
    pkt = wol.magic_packet("aa-bb-cc-dd-ee-ff")
    assert len(pkt) == 102 and pkt[:6] == b"\xff" * 6 and pkt[6:12] == bytes.fromhex("aabbccddeeff")
    with pytest.raises(ValueError):
        wol.parse_mac("not a mac")


def test_update_version_compare():
    assert update_check.newer("v1.2.0", "1.1.9")
    assert not update_check.newer("v1.1.0", "1.1.0")


def test_history_averages_buckets(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", str(tmp_path / "h.db"))
    monkeypatch.setattr(history, "_conn", None)
    monkeypatch.setattr(history, "_last", {})
    s = SimpleNamespace(id="m", mem_total=100, mem_used_fraction=0.5, cpu_percent=40.0, net_total_rate=10.0)
    now = time.time()
    history.record(s, now - 120)
    s.cpu_percent = 60.0
    history.record(s, now - 119)  # throttled: inside the 60s window, must be dropped
    history.record(s, now - 30)
    out = history.series("m", 3600, buckets=1, now=now)
    assert out["cpu"] == [50.0]  # (40 + 60) / 2: the throttled sample did not count
    assert out["mem"] == [0.5]


def test_tls_pin_trust_on_first_use(monkeypatch):
    pins = {}
    monkeypatch.setattr(store, "pinned_host_key", pins.get)
    monkeypatch.setattr(store, "pin_host_key", pins.__setitem__)
    tls_pin.check_der("h", 443, b"cert-a")
    tls_pin.check_der("h", 443, b"cert-a")
    assert pins["tlsCert.h:443"] == hashlib.sha256(b"cert-a").hexdigest()
    with pytest.raises(tls_pin.TLSPinError):
        tls_pin.check_der("h", 443, b"cert-b")


def test_alert_thresholds_follow_prefs(monkeypatch):
    from core import StorageItem
    from machine_session import MachineSession
    s = MachineSession(Machine(host="h", user="u"))
    s.storage = [StorageItem(id="d", title="D", subtitle="", size=100, used=90, healthy=True, statusText="")]
    monkeypatch.setattr(store, "pref", lambda k, d=None: {"alertStoragePct": 95}.get(k, d))
    assert not any(a.id == "storage-d" for a in s.alerts)
    monkeypatch.setattr(store, "pref", lambda k, d=None: d)
    assert any(a.id == "storage-d" for a in s.alerts)


def test_secret_roundtrip_and_forget():
    key = "secret.pytest-machine"
    store.set_secret(key, "hunter2")
    store._secret_cache.clear()  # force a real read-back, not the cache
    assert store.secret(key) == "hunter2"
    store.forget_secrets("pytest-machine")
    store._secret_cache.clear()
    assert store.secret(key) == ""
