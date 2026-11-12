"""Text limits used by the generator and the validator.

Avito Autoload has commonly capped the title at 50 characters. The description
cap depends on the category. 3000 is a conservative factory limit.
Confirm both against the current category spec before live publishing.
"""

TITLE_MAX_LENGTH = 50
DESCRIPTION_MAX_LENGTH = 3000
DESCRIPTION_MIN_WARNING = 80
