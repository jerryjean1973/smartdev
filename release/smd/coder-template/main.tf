terraform {
  required_providers {
    coder = {
      source  = "coder/coder"
      version = "2.20.0"
    }
    docker = {
      source  = "kreuzwerker/docker"
      version = "4.7.0"
    }
  }
}

provider "coder" {}
provider "docker" {}

data "coder_workspace" "current" {}
data "coder_workspace_owner" "current" {}

locals {
  deployment     = jsondecode(file("${path.module}/deployment.json"))
  project        = try(local.deployment.workspaces[data.coder_workspace.current.name], null)
  workspace_id   = data.coder_workspace.current.id
  workspace_name = data.coder_workspace.current.name
}

resource "coder_agent" "dev" {
  arch           = "amd64"
  os             = "linux"
  dir            = "/home/node"
  startup_script = file("${path.module}/start.sh")
}

resource "coder_app" "apps" {
  for_each = {
    editor      = { name = "VS Code", port = 13337 }
    codex-chat  = { name = "Codex Chat", port = 13338 }
    dev-preview = { name = "DEV Preview", port = 13339 }
  }
  agent_id     = coder_agent.dev.id
  slug         = each.key
  display_name = each.value.name
  url          = "http://127.0.0.1:${each.value.port}"
  subdomain    = false
  share        = "owner"
  healthcheck {
    url       = "http://127.0.0.1:${each.value.port}${lookup({editor="/healthz", dev-preview="/.__smartdev/health"}, each.key, "/")}"
    interval  = 5
    threshold = 3
  }
}

resource "docker_volume" "project" {
  name = "command-project-${local.workspace_id}"
}

resource "docker_volume" "home" {
  name = "command-home-${local.workspace_id}"
}

resource "docker_network" "project" {
  name = "command-net-${local.workspace_id}"
}

resource "docker_container" "workspace" {
  count       = local.project == null ? 0 : data.coder_workspace.current.start_count
  name        = "command-dev-${local.workspace_id}"
  image       = local.deployment.workspace_image
  entrypoint  = ["/usr/bin/tini", "--", "/bin/sh", "-c", coder_agent.dev.init_script]
  user        = "1000:1000"
  memory      = 4096
  memory_swap = 4096
  cpu_period  = 100000
  cpu_quota   = 150000
  security_opts = [
    "no-new-privileges:true",
    "apparmor=command-codex-workspace",
    "seccomp=${jsonencode(jsondecode(file("${path.module}/seccomp.json")))}",
  ]
  capabilities {
    drop = ["ALL"]
  }
  env = [
    "CODER_AGENT_TOKEN=${coder_agent.dev.token}",
    "PROJECT_NAME=${local.workspace_name}",
    "PROJECT_REPOSITORY=",
    "PROJECT_RUNTIME=${local.project.runtime}",
    "PROJECT_REVISION=${local.project.revision}",
    "PROJECT_SOURCE_REMOTE=https://github.com/${local.project.repository}.git",
    "PROJECT_PREVIEW_PATH=/@${data.coder_workspace_owner.current.name}/${local.workspace_name}/apps/dev-preview",
  ]
  labels {
    label = "smartdev.project.id"
    value = local.project.id
  }
  labels {
    label = "smartdev.project.revision"
    value = local.project.revision
  }
  networks_advanced {
    name = docker_network.project.name
  }
  volumes {
    container_path = "/run/selected-source.bundle"
    host_path      = "${local.deployment.data_dir}/workspace-seeds/${local.project.id}/${local.project.revision}.bundle"
    read_only      = true
  }
  volumes {
    container_path = "/run/command-memory"
    host_path      = "${local.deployment.data_dir}/workspace-config/${local.workspace_name}"
    read_only      = true
  }
  volumes {
    container_path = "/projects"
    volume_name    = docker_volume.project.name
  }
  volumes {
    container_path = "/home/node"
    volume_name    = docker_volume.home.name
  }
}
