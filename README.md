# ha-git-config

Deliver a Home Assistant configuration from Git, one way. A GitHub Action checks
a configuration repository and publishes it, together with its custom
integrations and dashboard cards, as an OCI image. Kubernetes mounts that image
read-only into the Home Assistant pod as an
[image volume](https://kubernetes.io/docs/concepts/storage/volumes/#image).

The image bundles everything the configuration depends on. The Action downloads
each custom integration and card pinned in `hacs.yaml`, verifies it and
installs it into the image, so Home Assistant needs neither HACS nor network
access to GitHub at runtime. To update a dependency, change its pin in Git.

Git is the only way to change the YAML configuration. The mounts below block
Home Assistant's UI editors from saving automations, scripts and scenes.
Creation actions may still appear in the UI, but saving fails. Everything Home
Assistant keeps in `.storage`, such as dashboards, integrations set up in the UI
and the entity registry, stays on the persistent volume and remains editable.

## Configuration repository

```text
configuration.yaml
includes/      files configuration.yaml includes, such as automations.yaml
packages/
blueprints/
pyscript/
hacs.yaml      custom integrations and dashboard cards
```

Only these paths reach Home Assistant. Each is mounted on its own, so files that
`configuration.yaml` includes live in `includes/` rather than next to it, for
example `automation: !include includes/automations.yaml`. Adding a file to a
directory never needs a change to the mounts.

### hacs.yaml

```yaml
integrations:
  - repository: amitfin/retry
    version: v4.2.0
    asset: retry.zip
  - repository: custom-components/nordpool
    version: 0.0.18
    commit: 1eaa2490943bb41a2e322e9349ba2d7c6746a94b

cards:
  - repository: RomRider/apexcharts-card
    version: v2.2.3
    asset: apexcharts-card.js
  - repository: mlamberts78/weather-chart-card
    version: V2.4.11
    commit: 71be08feeced42427ac69954168ff4ba05990dfc
    path: dist/weather-chart-card.js
```

Each entry is one of two kinds, with its keys in this order so Renovate can
update it:

- `asset`: a file attached to the GitHub release `version`. The download must
  match the SHA-256 digest GitHub publishes for the asset. An integration asset
  is a zip with `manifest.json` at its root.
- `commit`: the repository at that commit, fetched with Git. The tag `version`
  must point to it. An integration is the single directory under
  `custom_components/`. A card takes the file or directory at `path`.

Integrations go to `custom_components/<domain>`, cards to
`www/community/<repository name>`. Reference cards as dashboard resources at
`/local/community/<repository name>/<file>`.

## Workflow

```yaml
name: Config

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

concurrency:
  group: config-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  config:
    runs-on: ubuntu-26.04
    timeout-minutes: 20
    permissions:
      contents: read
      packages: write
    steps:
      - name: Check and publish the configuration
        uses: mcsrobert/ha-git-config@<commit> # v0.1.0
        with:
          # renovate: datasource=pypi depName=homeassistant
          home-assistant-version: 2026.10.4
          push: ${{ github.ref == 'refs/heads/main' }}
```

The action:

1. Scans the Git history for secrets with gitleaks.
2. Builds the runtime layout from the repository and `hacs.yaml`.
3. Runs Home Assistant's `check_config` on it and fails on any error or warning.
4. Builds a `FROM scratch` image for `linux/amd64` and `linux/arm64`. With
   `push: true` it pushes `ghcr.io/<owner>/<repository>:main` and
   `:sha-<commit>`.

The image is reproducible: the same files give the same digest, so a commit
that changes nothing Home Assistant reads does not restart it.

## Renovate

Extend the preset in the configuration repository to update `hacs.yaml` and
annotated versions such as `home-assistant-version`:

```json5
{
  extends: ['config:recommended', 'helpers:pinGitHubActionDigests', 'github>mcsrobert/ha-git-config'],
}
```

## Mounting the image

The image holds these paths, including the integrations and cards from
`hacs.yaml` under `custom_components` and `www/community`. Mount each one over
`/config`, which stays a persistent volume for `.storage`, the database and
logs. The mounts are read-only, so HACS cannot manage those two directories:

```yaml
volumes:
  - name: config-git
    image:
      reference: ghcr.io/<owner>/<repository>:main@sha256:<digest>
containers:
  - name: home-assistant
    volumeMounts:
      - { name: config-git, mountPath: /config/configuration.yaml, subPath: configuration.yaml }
      - { name: config-git, mountPath: /config/automations.yaml, subPath: editor-guards/automations.yaml }
      - { name: config-git, mountPath: /config/scripts.yaml, subPath: editor-guards/scripts.yaml }
      - { name: config-git, mountPath: /config/scenes.yaml, subPath: editor-guards/scenes.yaml }
      - { name: config-git, mountPath: /config/includes, subPath: includes }
      - { name: config-git, mountPath: /config/packages, subPath: packages }
      - { name: config-git, mountPath: /config/blueprints, subPath: blueprints }
      - { name: config-git, mountPath: /config/pyscript, subPath: pyscript }
      - { name: config-git, mountPath: /config/custom_components, subPath: custom_components }
      - { name: config-git, mountPath: /config/www/community, subPath: www/community }
```

The image generates empty `automations.yaml`, `scripts.yaml` and `scenes.yaml`
under `editor-guards/`. Mount all three over their fixed `/config` paths, even
when the configuration loads those domains from `includes/` or packages. Home
Assistant's UI editors always write to these root paths; moving the includes
does not change that. Without these mounts, the editors can report a successful
save to the persistent volume while Home Assistant continues loading the
unchanged Git configuration. Keep the actual YAML content in `includes/` or
packages, not in these generated guard files.

Pin the image by digest and let Renovate update it. A new digest rolls the pod,
so every configuration change restarts Home Assistant. Image volumes need
Kubernetes 1.33 or later with a container runtime that supports them; `subPath`
needs 1.33 too.

## Development

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests
```
