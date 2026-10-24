# A design

A design is an assembly: parts placed as instances, held together by mates, relations and fastener sets that every build verifies. Its folder contains:

- `design.json`: the structure (parts, their connectors, the mates between them, standard parts to buy), the parameters, configurations, requirements and brief
- `interfaces.py`: the mating values two parts share, computed once
- `parts/`: the code that builds each part in its own frame, the way it is made
- `assembly.py`: where each instance sits

Your agent writes all of them, structure first. **build123d** produces exact solids with STEP export and measured fits, which suits mechanisms, enclosures and fixtures. **Blender** produces meshes, which suit sculpted forms; a design can mix both.
