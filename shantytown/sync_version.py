"""A host-sync protocol floor independent of development package versions."""

# Version 1 promises host filtering and administrator-demotion refusal.
# Several incompatible builds shared 0.4.0+dev; package version is not evidence.
HOST_SYNC_VERSION = 1


def require_host_sync(required: int) -> None:
    if required > HOST_SYNC_VERSION:
        raise ValueError(
            f"host-sync protocol {required} required; this build supports "
            f"{HOST_SYNC_VERSION}. Upgrade shantytown on this host before roles sync; "
            "do not remove the requirement to bypass it.")
