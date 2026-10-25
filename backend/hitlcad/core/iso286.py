"""ISO 286-1:2010 limits and fits, from the standard's tables: the standard tolerance grades IT1 … IT18 and the
fundamental deviations of holes A … ZC and shafts a … zc, for nominal sizes up to 3150 mm. A tolerance class's limit
deviations follow from them by the standard's rules; ISO 286-2 tabulates the results for the classes in common use,
and agrees with these. A class the standard does not define at a size raises `ValueError`, saying what it defines."""

from __future__ import annotations

import re

MAX_SIZE_MM = 3150

#: ISO 286-1 Table 1: standard tolerance values in µm, IT1 … IT18, by nominal size range (above the previous bound,
#: up to and including this one, in mm).
IT: dict[int, tuple[float, ...]] = {
    3: (0.8, 1.2, 2, 3, 4, 6, 10, 14, 25, 40, 60, 100, 140, 250, 400, 600, 1000, 1400),
    6: (1, 1.5, 2.5, 4, 5, 8, 12, 18, 30, 48, 75, 120, 180, 300, 480, 750, 1200, 1800),
    10: (1, 1.5, 2.5, 4, 6, 9, 15, 22, 36, 58, 90, 150, 220, 360, 580, 900, 1500, 2200),
    18: (1.2, 2, 3, 5, 8, 11, 18, 27, 43, 70, 110, 180, 270, 430, 700, 1100, 1800, 2700),
    30: (1.5, 2.5, 4, 6, 9, 13, 21, 33, 52, 84, 130, 210, 330, 520, 840, 1300, 2100, 3300),
    50: (1.5, 2.5, 4, 7, 11, 16, 25, 39, 62, 100, 160, 250, 390, 620, 1000, 1600, 2500, 3900),
    80: (2, 3, 5, 8, 13, 19, 30, 46, 74, 120, 190, 300, 460, 740, 1200, 1900, 3000, 4600),
    120: (2.5, 4, 6, 10, 15, 22, 35, 54, 87, 140, 220, 350, 540, 870, 1400, 2200, 3500, 5400),
    180: (3.5, 5, 8, 12, 18, 25, 40, 63, 100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300),
    250: (4.5, 7, 10, 14, 20, 29, 46, 72, 115, 185, 290, 460, 720, 1150, 1850, 2900, 4600, 7200),
    315: (6, 8, 12, 16, 23, 32, 52, 81, 130, 210, 320, 520, 810, 1300, 2100, 3200, 5200, 8100),
    400: (7, 9, 13, 18, 25, 36, 57, 89, 140, 230, 360, 570, 890, 1400, 2300, 3600, 5700, 8900),
    500: (8, 10, 15, 20, 27, 40, 63, 97, 155, 250, 400, 630, 970, 1550, 2500, 4000, 6300, 9700),
    630: (9, 11, 16, 22, 32, 44, 70, 110, 175, 280, 440, 700, 1100, 1750, 2800, 4400, 7000, 11000),
    800: (10, 13, 18, 25, 36, 50, 80, 125, 200, 320, 500, 800, 1250, 2000, 3200, 5000, 8000, 12500),
    1000: (11, 15, 21, 28, 40, 56, 90, 140, 230, 360, 560, 900, 1400, 2300, 3600, 5600, 9000, 14000),
    1250: (13, 18, 24, 33, 47, 66, 105, 165, 260, 420, 660, 1050, 1650, 2600, 4200, 6600, 10500, 16500),
    1600: (15, 21, 29, 39, 55, 78, 125, 195, 310, 500, 780, 1250, 1950, 3100, 5000, 7800, 12500, 19500),
    2000: (18, 25, 35, 46, 65, 92, 150, 230, 370, 600, 920, 1500, 2300, 3700, 6000, 9200, 15000, 23000),
    2500: (22, 30, 41, 55, 78, 110, 175, 280, 440, 700, 1100, 1750, 2800, 4400, 7000, 11000, 17500, 28000),
    3150: (26, 36, 50, 68, 96, 135, 210, 330, 540, 860, 1350, 2100, 3300, 5400, 8600, 13500, 21000, 33000),
}

#: ISO 286-1 Tables 2 and 3: fundamental deviations of shafts in µm, by size range as for `IT` (a value holds up to
#: and including its bound, from the one before; past the last bound, or where the value is None, the letter is not
#: defined): the upper deviation es of a … h, the lower ei of k … zc. k's holds for grades 4 … 7 (0 for the others).
SHAFT: dict[str, dict[int, float | None]] = {
    "a": {1: None, 3: -270, 6: -270, 10: -280, 18: -290, 30: -300, 40: -310, 50: -320, 65: -340, 80: -360,
          100: -380, 120: -410, 140: -460, 160: -520, 180: -580, 200: -660, 225: -740, 250: -820, 280: -920,
          315: -1050, 355: -1200, 400: -1350, 450: -1500, 500: -1650},
    "b": {1: None, 3: -140, 6: -140, 10: -150, 18: -150, 30: -160, 40: -170, 50: -180, 65: -190, 80: -200,
          100: -220, 120: -240, 140: -260, 160: -280, 180: -310, 200: -340, 225: -380, 250: -420, 280: -480,
          315: -540, 355: -600, 400: -680, 450: -760, 500: -840},
    "c": {3: -60, 6: -70, 10: -80, 18: -95, 30: -110, 40: -120, 50: -130, 65: -140, 80: -150, 100: -170, 120: -180,
          140: -200, 160: -210, 180: -230, 200: -240, 225: -260, 250: -280, 280: -300, 315: -330, 355: -360,
          400: -400, 450: -440, 500: -480},
    "cd": {3: -34, 6: -46, 10: -56},
    "d": {3: -20, 6: -30, 10: -40, 18: -50, 30: -65, 50: -80, 80: -100, 120: -120, 180: -145, 250: -170, 315: -190,
          400: -210, 500: -230, 630: -260, 800: -290, 1000: -320, 1250: -350, 1600: -390, 2000: -430, 2500: -480,
          3150: -520},
    "e": {3: -14, 6: -20, 10: -25, 18: -32, 30: -40, 50: -50, 80: -60, 120: -72, 180: -85, 250: -100, 315: -110,
          400: -125, 500: -135, 630: -145, 800: -160, 1000: -170, 1250: -195, 1600: -220, 2000: -240, 2500: -260,
          3150: -290},
    "ef": {3: -10, 6: -14, 10: -18},
    "f": {3: -6, 6: -10, 10: -13, 18: -16, 30: -20, 50: -25, 80: -30, 120: -36, 180: -43, 250: -50, 315: -56,
          400: -62, 500: -68, 630: -76, 800: -80, 1000: -86, 1250: -98, 1600: -110, 2000: -120, 2500: -130,
          3150: -145},
    "fg": {3: -4, 6: -6, 10: -8},
    "g": {3: -2, 6: -4, 10: -5, 18: -6, 30: -7, 50: -9, 80: -10, 120: -12, 180: -14, 250: -15, 315: -17, 400: -18,
          500: -20, 630: -22, 800: -24, 1000: -26, 1250: -28, 1600: -30, 2000: -32, 2500: -34, 3150: -38},
    "h": {3150: 0},
    "k": {3: 0, 6: 1, 10: 1, 18: 1, 30: 2, 50: 2, 80: 2, 120: 3, 180: 3, 250: 4, 315: 4, 400: 4, 500: 5, 3150: 0},
    "m": {3: 2, 6: 4, 10: 6, 18: 7, 30: 8, 50: 9, 80: 11, 120: 13, 180: 15, 250: 17, 315: 20, 400: 21, 500: 23,
          630: 26, 800: 30, 1000: 34, 1250: 40, 1600: 48, 2000: 58, 2500: 68, 3150: 76},
    "n": {3: 4, 6: 8, 10: 10, 18: 12, 30: 15, 50: 17, 80: 20, 120: 23, 180: 27, 250: 31, 315: 34, 400: 37, 500: 40,
          630: 44, 800: 50, 1000: 56, 1250: 66, 1600: 78, 2000: 92, 2500: 110, 3150: 135},
    "p": {3: 6, 6: 12, 10: 15, 18: 18, 30: 22, 50: 26, 80: 32, 120: 37, 180: 43, 250: 50, 315: 56, 400: 62, 500: 68,
          630: 78, 800: 88, 1000: 100, 1250: 120, 1600: 140, 2000: 170, 2500: 195, 3150: 240},
    "r": {3: 10, 6: 15, 10: 19, 18: 23, 30: 28, 50: 34, 65: 41, 80: 43, 100: 51, 120: 54, 140: 63, 160: 65, 180: 68,
          200: 77, 225: 80, 250: 84, 280: 94, 315: 98, 355: 108, 400: 114, 450: 126, 500: 132, 560: 150, 630: 155,
          710: 175, 800: 185, 900: 210, 1000: 220, 1120: 250, 1250: 260, 1400: 300, 1600: 330, 1800: 370,
          2000: 400, 2240: 440, 2500: 460, 2800: 550, 3150: 580},
    "s": {3: 14, 6: 19, 10: 23, 18: 28, 30: 35, 50: 43, 65: 53, 80: 59, 100: 71, 120: 79, 140: 92, 160: 100,
          180: 108, 200: 122, 225: 130, 250: 140, 280: 158, 315: 170, 355: 190, 400: 208, 450: 232, 500: 252,
          560: 280, 630: 310, 710: 340, 800: 380, 900: 430, 1000: 470, 1120: 520, 1250: 580, 1400: 640, 1600: 720,
          1800: 820, 2000: 920, 2240: 1000, 2500: 1100, 2800: 1250, 3150: 1400},
    "t": {24: None, 30: 41, 40: 48, 50: 54, 65: 66, 80: 75, 100: 91, 120: 104, 140: 122, 160: 134, 180: 146,
          200: 166, 225: 180, 250: 196, 280: 218, 315: 240, 355: 268, 400: 294, 450: 330, 500: 360, 560: 400,
          630: 450, 710: 500, 800: 560, 900: 620, 1000: 680, 1120: 780, 1250: 840, 1400: 960, 1600: 1050,
          1800: 1200, 2000: 1350, 2240: 1500, 2500: 1650, 2800: 1900, 3150: 2100},
    "u": {3: 18, 6: 23, 10: 28, 18: 33, 24: 41, 30: 48, 40: 60, 50: 70, 65: 87, 80: 102, 100: 124, 120: 144,
          140: 170, 160: 190, 180: 210, 200: 236, 225: 258, 250: 284, 280: 315, 315: 350, 355: 390, 400: 435,
          450: 490, 500: 540, 560: 600, 630: 660, 710: 740, 800: 840, 900: 940, 1000: 1050, 1120: 1150, 1250: 1300,
          1400: 1450, 1600: 1600, 1800: 1850, 2000: 2000, 2240: 2300, 2500: 2500, 2800: 2900, 3150: 3200},
    "v": {14: None, 18: 39, 24: 47, 30: 55, 40: 68, 50: 81, 65: 102, 80: 120, 100: 146, 120: 172, 140: 202,
          160: 228, 180: 252, 200: 284, 225: 310, 250: 340, 280: 385, 315: 425, 355: 475, 400: 530, 450: 595,
          500: 660},
    "x": {3: 20, 6: 28, 10: 34, 14: 40, 18: 45, 24: 54, 30: 64, 40: 80, 50: 97, 65: 122, 80: 146, 100: 178,
          120: 210, 140: 248, 160: 280, 180: 310, 200: 350, 225: 385, 250: 425, 280: 475, 315: 525, 355: 590,
          400: 660, 450: 740, 500: 820},
    "y": {18: None, 24: 63, 30: 75, 40: 94, 50: 114, 65: 144, 80: 174, 100: 214, 120: 254, 140: 300, 160: 340,
          180: 380, 200: 425, 225: 470, 250: 520, 280: 580, 315: 650, 355: 730, 400: 820, 450: 920, 500: 1000},
    "z": {3: 26, 6: 35, 10: 42, 14: 50, 18: 60, 24: 73, 30: 88, 40: 112, 50: 136, 65: 172, 80: 210, 100: 258,
          120: 310, 140: 365, 160: 415, 180: 465, 200: 520, 225: 575, 250: 640, 280: 710, 315: 790, 355: 900,
          400: 1000, 450: 1100, 500: 1250},
    "za": {3: 32, 6: 42, 10: 52, 14: 64, 18: 77, 24: 98, 30: 118, 40: 148, 50: 180, 65: 226, 80: 274, 100: 335,
           120: 400, 140: 470, 160: 535, 180: 600, 200: 670, 225: 740, 250: 820, 280: 920, 315: 1000, 355: 1150,
           400: 1300, 450: 1450, 500: 1600},
    "zb": {3: 40, 6: 50, 10: 67, 14: 90, 18: 108, 24: 136, 30: 160, 40: 200, 50: 242, 65: 300, 80: 360, 100: 445,
           120: 525, 140: 620, 160: 700, 180: 780, 200: 880, 225: 960, 250: 1050, 280: 1200, 315: 1300, 355: 1500,
           400: 1650, 450: 1850, 500: 2100},
    "zc": {3: 60, 6: 80, 10: 97, 14: 130, 18: 150, 24: 188, 30: 218, 40: 274, 50: 325, 65: 405, 80: 480, 100: 585,
           120: 690, 140: 800, 160: 900, 180: 1000, 200: 1150, 225: 1250, 250: 1350, 280: 1550, 315: 1700,
           355: 1900, 400: 2100, 450: 2400, 500: 2600},
}  # fmt: skip

#: j and J are tabulated per grade: the shaft's lower deviation ei, the hole's upper deviation ES, in µm.
J_SHAFT: dict[int, dict[int, float | None]] = {
    5: {3: -2, 6: -2, 10: -2, 18: -3, 30: -4, 50: -5, 80: -7, 120: -9, 180: -11, 250: -13, 315: -16, 400: -18,
        500: -20},
    6: {3: -2, 6: -2, 10: -2, 18: -3, 30: -4, 50: -5, 80: -7, 120: -9, 180: -11, 250: -13, 315: -16, 400: -18,
        500: -20},
    7: {3: -4, 6: -4, 10: -5, 18: -6, 30: -8, 50: -10, 80: -12, 120: -15, 180: -18, 250: -21, 315: -26, 400: -28,
        500: -32},
    8: {3: -6},
}  # fmt: skip
J_HOLE: dict[int, dict[int, float | None]] = {
    6: {3: 2, 6: 5, 10: 5, 18: 6, 30: 8, 50: 10, 80: 13, 120: 16, 180: 18, 250: 22, 315: 25, 400: 29, 500: 33},
    7: {3: 4, 6: 6, 10: 8, 18: 10, 30: 12, 50: 14, 80: 18, 120: 22, 180: 26, 250: 30, 315: 36, 400: 39, 500: 43},
    8: {3: 6, 6: 10, 10: 12, 18: 15, 30: 20, 50: 24, 80: 28, 120: 34, 180: 41, 250: 47, 315: 55, 400: 60, 500: 66},
}  # fmt: skip

LETTERS = [*list(SHAFT)[: list(SHAFT).index("k")], "js", "j", *list(SHAFT)[list(SHAFT).index("k") :]]
_CLASS = re.compile(r"^([A-Z]{1,2}|[a-z]{1,2})(\d{1,2})$")
_FIT = re.compile(r"^\s*([A-Z]{1,2}\d{1,2})\s*/\s*([a-z]{1,2}\d{1,2})\s*$")
SUPPORTED = (
    f"holes {', '.join(x.upper() for x in LETTERS)} and shafts {', '.join(LETTERS)}, grades 1 to 18, at sizes "
    f"above 0 up to {MAX_SIZE_MM} mm"
)


def parse(fit: str) -> tuple[str, str]:
    """A fit's (hole class, shaft class), e.g. ('H7', 'g6'); `ValueError` for anything ISO 286 does not define."""
    m = _FIT.match(fit)
    if not m:
        raise ValueError(f"{fit!r} is not an ISO 286 fit like 'H7/g6' (hole class / shaft class)")
    for cls in m.groups():
        _split(cls)
    return m.group(1), m.group(2)


def _split(cls: str) -> tuple[str, int]:
    """A tolerance class's (letter, grade); `ValueError` for one ISO 286 does not define."""
    m = _CLASS.match(cls)
    if not m:
        raise ValueError(f"{cls!r} is not an ISO 286 tolerance class like 'H7' or 'g6'")
    letter, grade = m.group(1), int(m.group(2))
    if letter.lower() not in LETTERS or not 1 <= grade <= 18:
        raise ValueError(f"{cls} is not an ISO 286 tolerance class; ISO 286 defines {SUPPORTED}")
    return letter, grade


def _at(table: dict[int, float | None], size: float) -> float | None:
    return next((v for bound, v in table.items() if size <= bound), None)


def deviations(cls: str, size_mm: float) -> tuple[float, float]:
    """(upper, lower) limit deviations in mm of a tolerance class at a nominal size: its grade's IT apart."""
    letter, grade = _split(cls)
    if not 0 < size_mm <= MAX_SIZE_MM:
        raise ValueError(f"Ø{size_mm:g} is outside ISO 286's sizes, above 0 up to {MAX_SIZE_MM} mm")
    if grade >= 14 and size_mm <= 1:
        raise ValueError(f"{cls}: grades IT14 to IT18 are not used at 1 mm or less")
    it = _at(IT, size_mm)[grade - 1]  # type: ignore[index]
    upper = _upper(cls, letter, grade, size_mm, it)
    return round(upper / 1000, 5), round((upper - it) / 1000, 5)  # to 0.01 µm: js deviations are quarter µm


def _upper(cls: str, letter: str, grade: int, size: float, it: float) -> float:
    """The upper deviation in µm."""
    hole, lower = letter.isupper(), letter.lower()
    if lower == "js":
        return it / 2
    if lower == "j":
        grades = J_HOLE if hole else J_SHAFT
        if grade not in grades:
            raise ValueError(f"ISO 286 defines {letter} in grades {', '.join(map(str, grades))} only, not {cls}")
        value = _at(grades[grade], size)
        if value is None:
            raise _undefined(cls, size, grades[grade])
        return value if hole else value + it
    fundamental = _at(SHAFT[lower], size)
    if fundamental is None:
        raise _undefined(cls, size, SHAFT[lower])
    if lower <= "h":  # a … h: the table gives es; A … H mirror it, EI = −es
        return it - fundamental if hole else fundamental
    if hole:
        return _hole_upper(cls, lower, grade, size, fundamental)
    return (fundamental if lower != "k" or 4 <= grade <= 7 else 0.0) + it  # k … zc: the table gives ei


def _hole_upper(cls: str, letter: str, grade: int, size: float, ei: float) -> float:
    """K … ZC: ES = −ei of the shaft, plus Δ = ITn − IT(n−1) for the finer grades up to 500 mm (K, M, N to IT8,
    P … ZC to IT7), and the standard's exceptions."""
    if grade < 3:
        raise ValueError(f"{cls}: ISO 286 defines {letter.upper()} from grade 3")
    if letter == "k":
        if grade > 8 and size > 3:
            raise ValueError(f"{cls}: ISO 286 defines K above grade 8 only up to 3 mm")
        ei = _at(SHAFT["k"], size) or 0.0  # K takes the k value of grades 4 … 7, whatever its own grade
    if letter == "n" and grade > 8 and size <= 1:
        raise ValueError(f"{cls}: ISO 286 does not use N above grade 8 at 1 mm or less")
    if size <= 500 and grade <= (8 if letter in ("k", "m", "n") else 7):
        grades = _at(IT, size)
        delta = 0.0 if size <= 3 else grades[grade - 1] - grades[grade - 2]  # type: ignore[index]
        es = -9.0 if cls == "M6" and 250 < size <= 315 else -ei + delta
    elif letter == "n" and 3 < size <= 500:
        es = 0.0
    else:
        es = -ei
    return es


def _undefined(cls: str, size: float, table: dict[int, float | None]) -> ValueError:
    above = max((bound for bound, v in table.items() if v is None), default=0)
    return ValueError(f"ISO 286 does not define {cls} at Ø{size:g}, only above {above} up to {max(table)} mm")


def limits(fit: str, size_mm: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """The (upper, lower) limit deviations in mm of a fit's hole and of its shaft at a basic size."""
    hole, shaft = parse(fit)
    return deviations(hole, size_mm), deviations(shaft, size_mm)


def clearance(hole: tuple[float, float], shaft: tuple[float, float]) -> tuple[float, float]:
    """(least, greatest) diametral clearance in mm between a hole's and a shaft's (upper, lower) limit deviations;
    negative is interference."""
    (hole_hi, hole_lo), (shaft_hi, shaft_lo) = hole, shaft
    return round(hole_lo - shaft_hi, 5), round(hole_hi - shaft_lo, 5)


def fit_clearance(fit: str, size_mm: float) -> tuple[float, float]:
    """(least, greatest) diametral clearance in mm of `fit` at a basic size; negative is interference."""
    return clearance(*limits(fit, size_mm))
