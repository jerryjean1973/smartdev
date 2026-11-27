disable_mlock = true
api_addr = "http://openbao:8200"
cluster_addr = "https://openbao:8201"

storage "raft" {
  path = "/openbao/file"
  node_id = "smartdev-vault"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

audit "file" "smartdev" {
  options {
    file_path = "/openbao/file/audit.jsonl"
  }
}
