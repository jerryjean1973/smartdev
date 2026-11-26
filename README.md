# smartdev
## Command Center host installations

`SMARTDEV_HOST_INSTALLATION_CONFIG` is server-side JSON. It contains the
installation `zone`, `release_registry`, `public_bripe_ip`, the trusted
`release_public_key` and `mig_public_key`, and a `projects` map keyed by
existing main project UUID. Each entry supplies the enrollment `id`,
`host_public_key`, a `destination` profile, and the signed release and
central MIG grant under `request`. The grant must bind the plan including the
destination and the per-host policy.

Customer-facing destinations are SMD Hosting, AWS customer account, Azure
customer subscription, Docker Download, and Remote Host. SMD Hosting uses the
existing Vultr-backed operator integration and its managed access. Customers
supply cloud delegation only for AWS and Azure.

Cloud profiles include region, compute, network, storage, DNS, access, and a
cost summary. AWS and Azure profiles also require a delegation reference.
Remote Host profiles include hostname, port, access reference and identity
reference, and use SSH or WinRM. Keep credential material in the customer's
delegation or the enrolled operator's existing credential store.
Profile fields carry references only. Preflight validates the signed
configuration. Provider permission, quota, cost and connectivity checks still
require the destination executor.

Set `operator_project_id` to the existing Command Center operator project and
list only destinations supported by that enrolled operator in
`operator_destinations`. For Docker downloads, set `artifact_directory` to
the internal release artifact directory. The installer and Coder template are
stored there under their signed SHA-256 digests.
