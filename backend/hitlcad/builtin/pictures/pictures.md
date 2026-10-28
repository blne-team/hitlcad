# Designing from a picture

The human can give a picture of what a design should look like (a photo of a pet, a product shot, a sketch) and
say what matters in it. hitlcad compares the model with it on every build, from the side the picture shows.

## Declaring a reference

Save the picture under the design's `references/` folder. If the human pasted it into the chat only, ask them to
save it there: hitlcad reads files, not the chat. Then add a requirement whose text is what they said about it
and whose check is `silhouette`:

```json
{"text": "Looks like Max in this photo from the side: the floppy ears and the curled tail matter most",
 "check": {"kind": "silhouette", "image": "references/side.jpg", "view": "right", "height": "height",
           "min_overlap": 0.9, "max_deviation": 3}}
```

- `view` is the side the picture shows: front (from −Y), back, right (from +X), left, top (from +Z) or bottom.
  Ask the human when it is not obvious; a figurine usually faces −Y, so a portrait of its face is `front`.
- `height` is the subject's extent up the picture in mm: the one dimension the human gave ("she is 175 mm
  tall"). Name a parameter (`"height"`) so the reference scales with the design.
- The subject is placed standing on Z=0 and centred across the view (top and bottom views: centred on the
  origin). Model it there.
- One requirement per picture; two pictures from two sides (front and right) pin down a figure far better than one.

The subject is cut out of its background automatically: a PNG with a transparent background is used as it is
cut, a photo or render on a plain background is separated by colour, and a sketch is the area its lines
enclose. A busy background can cut out wrong: the first failing build's figure shows the outline found (blue).
If it misses part of the subject, ask the human for a cut-out PNG.

## Modelling against it

1. Build a skeleton early (a rough mass per body part). When its silhouette result fails, request
   `include_images=True` on `build_design` or on `explore_design` to view the existing build. The figure shows
   the picture on a millimetre grid in the design's axes, the reference outline (blue), the model's
   (orange) and the numbered spots where they part.
2. Read the landmarks off the grid: the top of the head, the ears' tips, the paws, the tail, the joints'
   centres. Write them into interfaces.py or parameters as named values, so the part code and the requirement
   follow the same numbers, and tell the human what you read.
3. Build the masses to those landmarks, then refine. Each spot in the message names what strays, how far and
   which way: `head (ear_left) stops 4.2 mm short of the reference towards +Z at X −18.0, Z 168.0` means the
   left ear must reach 4.2 mm higher. Fix the largest first; fix a centring offset before anything else.
4. A picture shows one side: depth along the view (a figure's thickness, seen from the front) comes from a
   second picture, the human's words or your judgement. State what you assumed.

The check measures outlines only, not what is inside them: eyes, markings and colours are the human's to judge,
from the previews and the panel. Keep the requirement's thresholds as the human accepts them: an expressive
figurine may pass at 0.85, a product's profile needs tighter. Do not loosen them to pass.
