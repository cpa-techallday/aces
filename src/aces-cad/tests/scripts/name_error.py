"""A NameError in the script (test fixture): the reported frames must name script.py only."""


def shape_of(p):
    return undefined_part_name  # noqa: F821 (a NameError on purpose)


def build(p):
    return shape_of(p)
