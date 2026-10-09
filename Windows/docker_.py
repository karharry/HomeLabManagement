"""Docker container listing, shared by TrueNAS/Linux/macOS sessions. Mirrors App/Backends/Docker.swift."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class DockerContainer:
    id: str
    name: str
    image: str
    state: str
    status: str
    ports: str
    labels: dict = field(default_factory=dict)
    cpu: str | None = None
    memory: str | None = None

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def truenas_app(self) -> str | None:
        p = self.labels.get("com.docker.compose.project", "")
        return p[3:] if p.startswith("ix-") else None


SAMPLE_SCRIPT = (
    "docker ps -a --format '{{json .}}'\n"
    "echo ---STATS---\n"
    "docker stats --no-stream --format '{{json .}}'\n"
)


def parse(text: str) -> list[DockerContainer]:
    containers: list[DockerContainer] = []
    stats: dict[str, tuple[str, str]] = {}
    in_stats = False
    for raw in text.splitlines():
        line = raw.strip("\r")
        if line == "---STATS---":
            in_stats = True
            continue
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if in_stats:
            name = obj.get("Name")
            if name:
                mem = (obj.get("MemUsage") or "").split(" / ")[0]
                stats[name] = (obj.get("CPUPerc", ""), mem)
        else:
            labels = {}
            for kv in (obj.get("Labels") or "").split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    labels[k] = v
            containers.append(DockerContainer(
                id=obj.get("ID", ""), name=obj.get("Names", "?"), image=obj.get("Image", ""),
                state=obj.get("State", ""), status=obj.get("Status", ""), ports=obj.get("Ports", ""),
                labels=labels))
    for c in containers:
        if c.name in stats:
            c.cpu, c.memory = stats[c.name]
    containers.sort(key=lambda c: c.name.lower())
    return containers
