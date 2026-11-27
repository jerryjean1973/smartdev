"""Sources required by one verified managed SMD installation."""

def components(scripts):
    api=scripts.parent/"api"
    server=("runtime.py","smd_portal_generation.py","vault.py","domains.py","challenge_engine.py",
            "smd_owner_client.py","smd_preview_domains.py","codex_access.py","preview.py",
            "041_frozen_business_objectives.sql","development.py","deployment_portal_receiver.py","portal_handoff.py")
    return [*(api/name for name in server),*(scripts/name for name in
            ("enroll_deployment_portal.py","smd_handoff_cleanup.py","smd_custom_ingress.py"))]
