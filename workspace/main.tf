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

data "coder_external_auth" "github" {
  id       = "github"
  optional = true
}

data "coder_parameter" "repository" {
  name    = "repository"
  type    = "string"
  default = ""
  validation {
    regex = "^([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99})?$"
    error = "Enter a GitHub repository as owner/repo"
  }
}

data "coder_parameter" "runtime" {
  name    = "runtime"
  type    = "string"
  default = "node"
  validation {
    regex = "^(python|node|other)$"
    error = "Choose python, node or other"
  }
}

resource "coder_agent" "dev" {
  arch           = "amd64"
  os             = "linux"
  dir            = "/home/node"
  startup_script = "exec /usr/local/bin/start-project"
}

resource "coder_app" "editor" {
  agent_id     = coder_agent.dev.id
  slug         = "editor"
  display_name = "VS Code"
  url          = "http://127.0.0.1:13337"
  icon         = "/icon/code.svg"
  subdomain    = false
  share        = "owner"
  healthcheck {
    url       = "http://127.0.0.1:13337/healthz"
    interval  = 5
    threshold = 6
  }
}

resource "coder_app" "codex_chat" {
  agent_id     = coder_agent.dev.id
  slug         = "codex-chat"
  display_name = "Codex Chat"
  url          = "http://127.0.0.1:13338"
  subdomain    = false
  share        = "owner"
  healthcheck {
    url       = "http://127.0.0.1:13338/"
    interval  = 5
    threshold = 3
  }
}

resource "coder_app" "dev_preview" {
  agent_id     = coder_agent.dev.id
  slug         = "dev-preview"
  display_name = "DEV Preview"
  url          = "http://127.0.0.1:13339"
  subdomain    = false
  share        = "owner"
  healthcheck {
    url       = "http://127.0.0.1:13339/.__smartdev/health"
    interval  = 5
    threshold = 3
  }
}

resource "docker_volume" "projects" {
  name = "command-project-${data.coder_workspace.current.id}"
}

resource "docker_volume" "home" {
  name = "command-home-${data.coder_workspace.current.id}"
}

resource "docker_network" "workspace" {
  name = "command-net-${data.coder_workspace.current.id}"
}

resource "docker_container" "dev" {
  count           = data.coder_workspace.current.start_count
  name            = "command-dev-${data.coder_workspace.current.id}"
  image           = "command-dev-workspace:execution-privacy-r9"
  entrypoint      = ["/usr/bin/tini", "--", "/bin/sh", "-c", coder_agent.dev.init_script]
  user            = "1000:1000"
  memory          = 4096
  memory_swap     = 4096
  cpu_period      = 100000
  cpu_quota       = 200000
  security_opts = [
    "no-new-privileges:true",
    "apparmor=command-codex-workspace",
    "seccomp=${jsonencode(jsondecode(file("${path.module}/security/seccomp.json")))}",
  ]
  capabilities {
    drop = ["ALL"]
  }
  env = [
    "CODER_AGENT_TOKEN=${coder_agent.dev.token}",
    "PROJECT_NAME=${data.coder_workspace.current.name}",
    "PROJECT_REPOSITORY=${data.coder_parameter.repository.value}",
    "PROJECT_RUNTIME=${data.coder_parameter.runtime.value}",
    "PROJECT_PREVIEW_PATH=/@${data.coder_workspace_owner.current.name}/${data.coder_workspace.current.name}/apps/dev-preview",
  ]
  networks_advanced {
    name = docker_network.workspace.name
  }
  volumes {
    container_path = "/run/command-memory"
    host_path      = "/var/lib/dev-command-center/workspace-config/${data.coder_workspace.current.name}"
    read_only      = true
  }
  volumes {
    container_path = "/projects"
    volume_name    = docker_volume.projects.name
  }
  volumes {
    container_path = "/home/node"
    volume_name    = docker_volume.home.name
  }
}
