"""The ~20-second speed test after a model download (PLAN §6.6).

Runs the real extraction prompt on a built-in sample ad, so the measured prompt and output
speeds match the work the model will do. The result replaces the table estimate.
"""

from adacar.ai.advisor import Speed
from adacar.ai.client import LlmClient, Timing
from adacar.ai.extract import extract

SAMPLE_AD = """Škoda Octavia Combi 2.0 TDI Style, 1. majitel
Kolín, 289 000 Kč

Prodám Octavii III combi 2.0 TDI 110 kW, rok výroby 2017, najeto 186 tis. km.
Koupeno v ČR jako nové, první majitel, servisní knížka, pravidelný servis
u autorizovaného dealera, poslední výměna oleje a filtrů před 8 tis. km.

Výbava:
• automatická klimatizace, vyhřívaná sedadla, tempomat
• navigace Columbus, parkovací senzory vpředu i vzadu, couvací kamera
• tažné zařízení, zimní pneu na discích
• převodovka DSG, 7 stupňů

Auto je nehavarované, bez koroze, STK do 2027. Na karoserii drobné škrábance odpovídající
stáří vozu. Důvod prodeje: koupě většího auta.
Při rychlém jednání sleva. Prohlídka možná v Kolíně po domluvě, ideálně o víkendu.
Na dotazy odpovím telefonicky, SMS nebo e-mailem."""
ROUNDS = 2


def run(client: LlmClient) -> Speed:
    """Two extractions: the first reads the whole prompt (no cache), the second shows the
    usual case where the shared instructions are already cached."""
    t = Timing.total([extract(client, SAMPLE_AD, untrusted=True).timing for _ in range(ROUNDS)])
    return Speed(prompt_tps=t.prompt_tps or 1.0, output_tps=t.output_tps or 1.0, measured=True)
