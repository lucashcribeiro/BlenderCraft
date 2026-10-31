"""Prompt engineering per style, for printable figurines."""

STYLES = ("figurine", "realistic", "toy-articulated")

# Appended to the user's subject. Every style asks for one solid, standing
# piece with no base or lettering: what prints well and what the post
# processing (scale, flat bottom, repair) expects.
_SUFFIX = {
    "figurine": (
        "collectible vinyl toy figurine, stylized cute proportions, smooth "
        "clean surfaces, big glossy eyes, standing on its own feet, compact "
        "pose with limbs close to the body, single solid piece, no base, no text"
    ),
    "realistic": (
        "realistic sculpture, accurate anatomy and proportions, detailed "
        "natural surface, standing in a neutral stable pose, single solid "
        "piece, no base, no text"
    ),
    "toy-articulated": (
        "toy action figure, chunky simplified shapes, thick sturdy limbs "
        "clearly separated from the body, neutral standing pose, smooth "
        "surfaces, single solid piece, no base, no text"
    ),
}

NEGATIVE = (
    "thin fragile parts, floating parts, multiple objects, base, pedestal, "
    "text, watermark, low quality"
)


def build_prompt(subject: str, style: str, limit: int) -> str:
    """The subject followed by the style's suffix, at most ``limit`` chars
    (the subject is shortened, the suffix kept)."""
    if style not in _SUFFIX:
        raise ValueError(f"Unknown style {style!r}; use one of {', '.join(STYLES)}")
    subject = " ".join(str(subject).split()).rstrip(" ,.")
    suffix = _SUFFIX[style]
    room = limit - len(suffix) - 2
    if room <= 0:
        return suffix[:limit]
    if len(subject) > room:
        subject = subject[:room].rstrip(" ,.")
    return f"{subject}, {suffix}" if subject else suffix
