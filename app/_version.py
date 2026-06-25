"""Einzige Quelle der Wahrheit für die iDash-Version.

Lokale Builds lesen diesen Wert; die CI überschreibt die Version aus dem
Git-Tag (vX.Y.Z) beim `vpk pack`. Bei einem Release also hier den Wert auf die
nächste Version setzen und denselben Tag pushen.
"""

__version__ = "0.1.0"
