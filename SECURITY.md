# Security policy

The intended supported series is 0.2.x. Report suspected vulnerabilities through **Security → Report a vulnerability** on the GitHub repository if private reporting is enabled. If that option is unavailable, request a private channel from a maintainer while keeping exploit details and sensitive recordings out of public issues.

Include the affected version, platform, conditions required to trigger the issue and impact. Use synthetic or redacted audio when possible. Coordinate public disclosure with maintainers so users have a chance to update.

The server binds to loopback by default. Host operators who enable remote access are responsible for authentication, transport security and process isolation. See the REST documentation for request and decode bounds.

For confirmed issues in a dependency, include the dependency version and advisory identifier. Test with a fresh virtual environment so unrelated system packages do not affect the result.
