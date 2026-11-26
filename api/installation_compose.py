"""Render the private installation stack from a verified plan."""

def compose(plan):
    if plan.get("release_profile") != "smd-host":
        raise ValueError("Expected a standalone SMD host release")
    images = plan["images"]
    volumes = plan["volumes"]
    credentials = "${SMD_CREDENTIALS_DIR:?Set SMD_CREDENTIALS_DIR to the host-local credentials directory}"

    def service(role, **options):
        return {"image": images[role], "restart": "unless-stopped", **options}

    return {
        "name": plan["host_id"],
        "services": {
            "postgres": service("postgres", env_file=[f"{credentials}/postgres.env"],
                                volumes=["postgres:/var/lib/postgresql/data"]),
            "redis": service("redis", command=["redis-server", "/etc/redis/redis.conf"],
                             volumes=["redis:/data", f"{credentials}/redis.conf:/etc/redis/redis.conf:ro"]),
            "openbao": service("openbao", command=["server", "-config=/etc/openbao/config.hcl"],
                               volumes=["openbao:/openbao/data", "./openbao.hcl:/etc/openbao/config.hcl:ro"]),
            "coder": service("coder", env_file=[f"{credentials}/coder.env"]),
            "api": service("api", env_file=[f"{credentials}/api.env"],
                           environment=plan["api_environment"],
                           volumes=[f"{credentials}/vault-token:/run/command-vault/token:ro"]),
            "worker": service("api", command=["python", "-m", "worker"],
                              env_file=[f"{credentials}/worker.env"],
                              environment=plan["api_environment"],
                              volumes=["workspace-config:/workspace-config"]),
            "portal": service("portal"),
        },
        "volumes": {role: {"name": volumes[role]} for role in
                    ("postgres", "redis", "openbao", "workspace-config")},
    }
