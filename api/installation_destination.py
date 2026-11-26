"""Validate customer deployment profiles before presenting them for approval."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter

Reference = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}$")]
Hostname = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")]


class Cloud(BaseModel, extra="forbid", frozen=True):
    region: Reference
    compute: Reference
    network: Reference
    storage: Reference
    dns: Hostname
    access: Reference
    cost_summary: Annotated[str, StringConstraints(min_length=1, max_length=300)]


class AWSDestination(Cloud):
    kind: Literal["aws"]
    delegation_ref: Reference


class AzureDestination(Cloud):
    kind: Literal["azure"]
    delegation_ref: Reference


class ManagedHostingDestination(Cloud):
    kind: Literal["vultr"]
    # Existing signed plans may reference operator-managed access here.
    delegation_ref: Reference | None = None


class RemoteDestination(BaseModel, extra="forbid", frozen=True):
    kind: Literal["ssh", "winrm"]
    hostname: Hostname
    port: int = Field(strict=True, ge=1, le=65535)
    access_ref: Reference
    identity_ref: Reference


class DownloadDestination(BaseModel, extra="forbid", frozen=True):
    kind: Literal["docker"]


Destination = TypeAdapter(Annotated[
    AWSDestination | AzureDestination | ManagedHostingDestination | RemoteDestination | DownloadDestination,
    Field(discriminator="kind"),
])


DESTINATION_LABELS = {
    "vultr": "SMD Hosting",
    "aws": "AWS customer account",
    "azure": "Azure customer subscription",
    "docker": "Docker Download",
    "ssh": "Remote Host",
    "winrm": "Remote Host",
}
