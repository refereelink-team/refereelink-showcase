# Third-Party Notices

## Roboflow source lineage

This repository retains code lineage from the original Roboflow soccer-analysis
project. The MIT License and the copyright notice in `LICENSE` remain in force
for the covered code and must be retained in redistributed copies.

## External datasets, models, and samples

External SoccerNet/MVFoul data, downloaded video samples, model weights, and
third-party model code are not bundled in this repository. Obtain and use them
only under their own licenses and terms. The repository does not grant rights
to redistribute those external materials.

## Dependencies

Python and frontend dependencies are resolved from `pyproject.toml`, `requirements-inference.txt`,
`package.json`, and `package-lock.json`; they are not vendored here. Each
dependency's own license and notice requirements continue to apply.

## Optional MultiDimStacker runtime

The optional adapter invokes the author's implementation at
https://github.com/druefena/MVFoul, licensed under GPL-3.0. That implementation,
its checkpoint and its extra dependencies are installed as external runtime
assets and are not redistributed in this repository. The adapter does not copy
the author's network source. The external implementation's license continues
to apply when installing or distributing a combined runtime.
