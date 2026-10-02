# Security

The current development line is `main`. There is no promise of support for older snapshots.

Report suspected vulnerabilities privately through the repository's GitHub **Security → Report a vulnerability** form. Include the affected commit, reproduction steps and impact with secrets and personal media removed. Do not disclose credentials, private recordings or exploit details in public issues. Maintainers will assess reports; no response-time guarantee is made.

## Deployment boundary

This is a trusted presentation and experiment service. The application does **not** provide user authentication or tenant isolation. Its APIs can launch GPU jobs, manage serial readers and proxy upstream device/review operations. Restrict access to a trusted VPN/private network, or enforce authentication and authorization at a reverse proxy plus firewall before exposing it. HTTPS alone does not authorize callers. Protect the upstream service as well.

Use one backend worker. Keep runtime media, model assets and host-specific `.env`/`deploy/showcase.env` outside Git; grant the service only necessary serial/filesystem access. Review dependencies and upstream model licenses independently. A CUDA check and a passing software suite do not establish hardware safety, referee decision accuracy or real-time performance.
