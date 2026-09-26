# Version Control System Guidelines

- **Always use Jujutsu (`jj`)** instead of `git` for all VCS operations in this repository (e.g. checking status, creating revisions/commits, inspecting history, and branching).
- The repository is configured as a colocated repo (`jj git init --colocate`), meaning changes made in `jj` are synchronized with git.
- Useful commands:
  - `jj status`: Inspect working copy status.
  - `jj describe -m "<message>"`: Set description / commit message for the current revision.
  - `jj new`: Start a new working-copy revision on top of the current change.
  - `jj bookmark create <name> -r @-` / `jj bookmark move <name> --to @`: Manage git branch pointers.
  - `jj git push`: Push bookmarks to git remotes.
