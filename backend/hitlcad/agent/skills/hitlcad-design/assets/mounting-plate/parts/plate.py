from hitlcad_build123d import mounting_plate


def build(p, i):
    return {
        "plate": mounting_plate(
            width=p["width"],
            depth=p["depth"],
            thickness=p["thickness"],
            holes=i.bolt_xy,
            hole_diameter=p["hole_diameter"],
            boss_height=p["boss_height"],
            boss_diameter=p["boss_diameter"],
        )
    }
