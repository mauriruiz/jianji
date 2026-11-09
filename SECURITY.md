# Security

## Reporting a vulnerability

Please report it privately, not in a public issue: on GitHub, **Security > Report a vulnerability** on this
repository, or e-mail me@mauriruiz.com. Include what you found, how to reproduce it and what it affects. jianji is
maintained by one person; expect a first reply within a week, and the fix in the next release.

## Scope

jianji runs on your own machine: a CLI that reads your footage and writes into the video folder, and Claude Code
skills that read text views of that footage. Worth reporting:

- anything that makes jian write outside `<video>/jianji/`, or modify footage or brief.md;
- a way for content in a video (speech, on-screen text, file names) to make jian or the skills run commands;
- a model or dependency loaded from somewhere other than what the README's Security section describes;
- secrets or personal data the stream privacy check should catch and doesn't.

Known and documented: the PyTorch 2.8 pin (README, Security).
