## Contribution Guidelines

Thank you for considering contributing to this project!
Contributions are always welcome and appreciated.

### How to Contribute

Please check the [issue tracker](https://github.com/habedi/duckdb-micro/issues) to see if there is an issue
you would like to work on or if it has already been resolved.

#### Reporting Bugs

1. Open an issue on the [issue tracker](https://github.com/habedi/duckdb-micro/issues).
2. Include information such as steps to reproduce the observed behavior and relevant logs or screenshots.

#### Suggesting Features

1. Open an issue on the [issue tracker](https://github.com/habedi/duckdb-micro/issues).
2. Provide details about the feature, its purpose, and potential implementation ideas.

### Submitting Pull Requests

- Make sure the Git hooks pass (`make test-hooks`) before submitting a pull request.
- If the change touches an experiment, verify it on the real board and say so in the description.
- Write a clear description of the changes you made and the reasons behind them.

> [!IMPORTANT]
> It's assumed that by submitting a pull request, you agree to license your contributions under the project's license.

### Development Workflow

> [!IMPORTANT]
> If you're using an AI-assisted coding tool like Claude Code or Codex, make sure the AI follows the instructions in the [AGENTS.md](AGENTS.md) file.

#### Prerequisites

Make sure you have [Nix](https://nixos.org/download.html) installed on your system.

```shell
nix develop
```

#### See Available Commands

- Run `make help` to see all available commands for managing different tasks.

### Code of Conduct

We adhere to the project's [Code of Conduct](CODE_OF_CONDUCT.md).
