# Execution security

PyDebugger treats every submitted program as untrusted. The supported backend
never evaluates student code with `exec`, `eval`, or a host Python subprocess.
It invokes Docker with an argument array and gives each run a unique container
name and a private temporary source directory.

## Enforced controls

- Network namespace disabled with `--network none`
- Read-only container root and read-only source bind mount
- Unprivileged UID/GID `65534:65534`
- All Linux capabilities dropped and `no-new-privileges` enabled
- Configured CPU, memory/swap, PID, file-descriptor, file-size, and tmpfs limits
- Python isolated mode and bytecode writes disabled
- Application and AI credentials are not passed into the container
- Bounded source and captured output; excessive output terminates the run
- Wall-clock deadline, user cancellation, forced container removal, and temp cleanup
- Immutable image digest and `--pull never` during student execution

## Deployment requirements

1. Run Linux containers. Pull the exact digest configured in `.env` before the
   application starts.
2. Prefer a rootless Docker daemon. For stronger isolation, configure an
   appropriate hardened runtime such as gVisor and set
   `PYDEBUGGER_DOCKER_RUNTIME=runsc` after validating it in the deployment.
3. Do not mount the Docker socket, application directories, cloud credentials,
   or host data into execution containers.
4. Keep execution workers separate from the web and AI-provider processes in a
   production deployment. The current Docker adapter is the boundary for that
   later service split.
5. Patch the pinned image deliberately: review a new digest, update
   `.env.example`, run the isolation tests, and deploy the image before changing
   production configuration.

Docker isolation reduces risk but does not make the host kernel invulnerable.
The daemon configuration and runtime remain part of the security boundary.

## Cancellation and cleanup

The application submits asynchronous execution jobs. Cancellation forcibly
removes the named container and terminates the Docker client process. Deadline,
output-limit, normal-completion, and error paths all attempt forced removal and
delete the temporary source directory. The runner never reuses a student
container.
