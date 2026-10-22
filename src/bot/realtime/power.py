"""Empêche la mise en veille automatique de Windows pendant que le bot tourne.

Vécu : le PC s'est endormi 6 min en plein paper trading (veille moderne) → bot figé, stop-loss non
surveillé. `SetThreadExecutionState` dit à Windows « un programme travaille » : la veille par
inactivité est bloquée. Une veille *volontaire* (capot fermé, bouton d'alimentation) reste possible :
pour du 24/7, un serveur (VPS) reste la vraie solution.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def prevent_sleep() -> Callable[[], None]:
    """Active la protection ; renvoie la fonction qui la retire (à appeler à l'arrêt du bot).

    À appeler depuis le fil principal (l'état est attaché au fil qui le pose). Sans effet hors Windows
    (sur un serveur Linux, il n'y a pas de veille automatique).
    """
    if sys.platform != "win32":
        return lambda: None
    import ctypes

    kernel32 = ctypes.windll.kernel32
    if not kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
        log.warning("Impossible d'empêcher la mise en veille de Windows")
        return lambda: None
    log.info("Mise en veille automatique de Windows bloquée tant que le bot tourne")
    return lambda: kernel32.SetThreadExecutionState(ES_CONTINUOUS)
