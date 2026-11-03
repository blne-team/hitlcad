# hitlcad-lasercut

A worked example of a hitlcad plugin, kept outside hitlcad: it adds laser cutting without a change to
hitlcad, and shows up in design.json's validation, the agents' skills, the panel and every build report.

- **A process**, `{"process": "laser_cut", "thickness_mm": 3}`: each part is checked as a flat plate of
  the sheet's thickness, with no hole narrower than the sheet is thick; holes come out the kerf larger
  than drawn, and outlines the kerf smaller, for fit checks.
- **A building block** part files import, `hitlcad_lasercut.blocks.plate`: a plate with its holes and
  their hole connectors.
- **A skill reference**, `lasercut.md`, for the agents.

A project whose designs use it names it in its `hitlcad.toml`, so where it is not installed the panel says
what to install, and a design of another project that uses it fails to build until its agent adds it:

```toml
[plugins]
hitlcad-lasercut = ">=0.1"
```

Install it into hitlcad's environment (`uv tool install … --with hitlcad-lasercut`, see hitlcad's
INSTALL.md); hitlcad's own development environment has it already.
