path "smartdev/metadata/projects/*" {
  capabilities = ["read", "list"]
}

path "smartdev/data/projects/*" {
  capabilities = ["create", "read", "update"]
}

path "smartdev/delete/projects/*" {
  capabilities = ["update"]
}

path "smartdev/data/integrations/*" {
  capabilities = ["create", "read", "update"]
}

path "smartdev/metadata/integrations/*" {
  capabilities = ["delete"]
}

path "smartdev/metadata/projects/+/github-deploy/+" {
  capabilities = ["delete"]
}
