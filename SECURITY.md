# Security policy

Do not commit credentials to this repository. Use `.env` locally and a managed
secret store in deployment. Startup validation rejects missing placeholder
values and the settings object hides the Azure API key from its representation.

An earlier revision contained a Together API credential. Its literal has been
removed from the current tree, but Git history is not a secret store. The owner
must revoke that credential in the Together account and create a replacement if
the legacy experiments are still used. Rewriting Git history does not revoke a
credential and is therefore not a substitute for rotation.

Report suspected credential exposure privately to the repository owner. Do not
include live secrets, student code, or private course documents in an issue.
