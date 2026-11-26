"""Render the private installation stack from a verified plan."""

def compose(plan):
    images = plan["images"]
    volumes = plan["volumes"]

    def service(role, **options):
        return {"image": images[role], "restart": "unless-stopped", **options}

    return {
        "name": plan["host_id"],
        "services": {
            "postgres": service("postgres", env_file=["./credentials/postgres.env"],
                                volumes=["postgres:/var/lib/postgresql/data"]),
            "redis": service("redis", command=["redis-server", "/etc/redis/redis.conf"],
                             volumes=["redis:/data", "./credentials/redis.conf:/etc/redis/redis.conf:ro"]),
            "openbao": service("openbao", command=["server", "-config=/etc/openbao/config.hcl"],
                               volumes=["openbao:/openbao/data", "./openbao.hcl:/etc/openbao/config.hcl:ro"]),
            "coder": service("coder", env_file=["./credentials/coder.env"]),
            "api": service("api", env_file=["./credentials/api.env"],
                           environment=plan["api_environment"],
                           volumes=["./credentials/vault-token:/run/command-vault/token:ro"]),
            "worker": service("api", command=["python", "-m", "worker"],
                              env_file=["./credentials/worker.env"],
                              environment=plan["api_environment"],
                              volumes=["workspace-config:/workspace-config"]),
            "portal": service("portal"),
        },
        "volumes": {role: {"name": volumes[role]} for role in
                    ("postgres", "redis", "openbao", "workspace-config")},
    }
