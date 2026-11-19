"""UI texts. Czech is the default; English is selectable in the wizard and Settings."""

TEXTS: dict[str, dict[str, str]] = {
    "nav.search": {"cs": "Hledat", "en": "Search"},
    "wizard.title": {"cs": "Průvodce nastavením", "en": "Setup wizard"},
    "wizard.step": {"cs": "Krok {n} ze {total}", "en": "Step {n} of {total}"},
    "wizard.next": {"cs": "Další", "en": "Next"},
    "wizard.back": {"cs": "Zpět", "en": "Back"},
    "wizard.welcome": {
        "cs": "Všechna auta na prodej ve vašem okolí na jednom místě",
        "en": "Every car for sale near you, in one place",
    },
    "wizard.welcome.text": {
        "cs": "Aplikace prochází inzertní servery s auty a ukáže vám ta, která odpovídají "
        "tomu, co hledáte, i tomu, jak daleko jste ochotni za nimi jet. Nastavení "
        "zabere minutu.",
        "en": "The app reads car ad sites and shows you the cars that match what you want and "
        "how far you are willing to travel to see them. Setup takes a minute.",
    },
    "wizard.language": {"cs": "Jazyk aplikace", "en": "App language"},
    "wizard.theme": {"cs": "Barevné schéma", "en": "Colour scheme"},
    "theme.system": {"cs": "Podle Windows", "en": "Follow Windows"},
    "theme.light": {"cs": "Světlé", "en": "Light"},
    "theme.dark": {"cs": "Tmavé", "en": "Dark"},
    "theme.ocean": {"cs": "Oceán", "en": "Ocean"},
    "theme.forest": {"cs": "Les", "en": "Forest"},
    "theme.sunset": {"cs": "Západ slunce", "en": "Sunset"},
    "theme.plum": {"cs": "Švestka", "en": "Plum"},
    "theme.midnight": {"cs": "Půlnoc", "en": "Midnight"},
    "wizard.profile": {"cs": "Odkud vyrážíte", "en": "Where you start from"},
    "wizard.cars": {"cs": "Modely a cena", "en": "Models and price"},
    "wizard.cars.text": {
        "cs": "Jaká auta hledáte. Obojí je nepovinné a ve výsledcích to jde kdykoli změnit.",
        "en": "Which cars you are after. Both are optional and can be changed in the results at "
        "any time.",
    },
    "wizard.home": {
        "cs": "Odkud pojedete auta obhlédnout",
        "en": "Where you will travel from to see cars",
    },
    "wizard.home.hint": {
        "cs": "Přesná adresa nebo jen obec, např. „Kolín“. Slouží k výpočtu doby cesty k prodejci.",
        "en": "A precise address or just a town, e.g. “Kolín”. Used to compute the trip to a "
        "seller.",
    },
    "wizard.mode": {"cs": "Jak se k prodejci dopravíte", "en": "How you will get to a seller"},
    "mode.any": {"cs": "Nevím, ukaž vše", "en": "Not sure, show all"},
    "mode.car": {"cs": "Auto", "en": "Car"},
    "mode.transit": {"cs": "MHD a vlaky", "en": "Public transport"},
    "mode.walk": {"cs": "Pěšky", "en": "Walking"},
    "mode.bike": {"cs": "Kolo", "en": "Bike"},
    "wizard.max_minutes": {"cs": "Nejdelší cesta k prodejci", "en": "Longest trip to a seller"},
    "wizard.later": {
        "cs": "Vše můžete později změnit v Nastavení.",
        "en": "You can change all of this later in Settings.",
    },
    "wizard.done": {
        "cs": "Vše je nastaveno, můžeme stáhnout první auta",
        "en": "All set: let's download the first cars",
    },
    "wizard.done.text": {
        "cs": "Po kliknutí na Start projdeme vybrané inzertní servery pro kraje ve vašem "
        "dosahu. Čteme pomalu, aby nás nezablokovaly, takže první hledání může trvat i "
        "desítky minut. Auta v seznamu přibývají průběžně.",
        "en": "After Start, the chosen ad sites are read for the regions within your reach. "
        "They are read slowly so that they do not block us, so the first search can "
        "take tens of minutes. Cars appear in the list as they are found.",
    },
    "wizard.sources": {"cs": "Kde máme auta hledat", "en": "Where to look for cars"},
    "sources.title": {"cs": "Zdroje inzerátů", "en": "Ad sources"},
    "sources.text": {
        "cs": "Vyberte, odkud má aplikace auta stahovat.",
        "en": "Choose where the app downloads cars from.",
    },
    "sources.kind.portal": {"cs": "Autoportály", "en": "Car portals"},
    "sources.kind.classifieds": {"cs": "Inzertní servery", "en": "Classified ad boards"},
    "sources.classifieds.hint": {
        "cs": "Inzeráty od kohokoli. Ty, které auto neprodávají (výkup, „koupím“, náhradní "
        "díly), aplikace nezobrazí. Rok, nájezd a další údaje čte z textu inzerátu, "
        "takže u některých aut mohou chybět.",
        "en": "Ads by anyone. Those that sell no car (buyers, “I'll buy”, spare parts) are "
        "not shown. Year, mileage and other details are read from the ad's text, so "
        "some cars may lack them.",
    },
    "sources.off_default": {"cs": "ve výchozím stavu vypnuto", "en": "off by default"},
    "sources.consent": {
        "cs": "Rozumím riziku a chci tento zdroj zapnout",
        "en": "I understand the risk and want to switch this source on",
    },
    "sources.consent_missing": {
        "cs": "{source} se zapne, až zaškrtnete, že rozumíte riziku.",
        "en": "{source} is switched on only after you tick that you understand the risk.",
    },
    "sources.save": {"cs": "Uložit", "en": "Save"},
    "sources.saved": {
        "cs": "Uloženo. Projeví se při příštím hledání.",
        "en": "Saved. It applies from the next check.",
    },
    "wizard.auto_off": {
        "cs": "Automatické hledání je vypnuté. Zapnout ho můžete později v Nastavení.",
        "en": "Automatic checks are off. You can switch them on later in Settings.",
    },
    "wizard.start": {"cs": "Start", "en": "Start"},
    "wizard.skip": {"cs": "Přeskočit", "en": "Skip"},
    "minutes": {"cs": "min", "en": "min"},
    "search.placeholder": {
        "cs": "Model, výbava nebo místo – např. octavia kombi",
        "en": "Model, equipment or place – e.g. octavia estate",
    },
    "search.found": {"cs": "Nalezená auta: {n}", "en": "Cars found: {n}"},
    "search.empty": {
        "cs": "Zatím tu nejsou žádná auta. Klikněte na „Obnovit nabídky“.",
        "en": "No cars here yet. Click “Refresh offers”.",
    },
    "search.more": {"cs": "Další auta", "en": "More cars"},
    "search.empty_filtered": {
        "cs": "Těmto filtrům neodpovídá žádné auto. Zkuste některý filtr zrušit.",
        "en": "No car matches these filters. Try removing one.",
    },
    "filters.title": {"cs": "Filtry", "en": "Filters"},
    "view.list": {"cs": "Seznam", "en": "List"},
    "view.map": {"cs": "Mapa", "en": "Map"},
    "map.empty": {
        "cs": "Auta zatím nemají určené místo. Doplní se po dokončení hledání.",
        "en": "The cars have no place yet. It is added when the search finishes.",
    },
    "map.approx": {
        "cs": "přibližná poloha (obec nebo okres)",
        "en": "approximate (town or district)",
    },
    "map.more": {
        "cs": "…a další auta: {n}. Upřesněte hledání nebo filtry.",
        "en": "…and {n} more cars. Narrow the search or the filters.",
    },
    "filters.loading": {"cs": "Načítám filtry…", "en": "Loading filters…"},
    "filters.reset": {"cs": "Zrušit filtry", "en": "Clear filters"},
    "filters.any": {"cs": "Nezáleží", "en": "Any"},
    "filters.posted": {"cs": "Vloženo", "en": "Posted"},
    "filters.days": {"cs": "za posledních {n} dní", "en": "in the last {n} days"},
    "filters.day1": {"cs": "za poslední den", "en": "in the last day"},
    "filters.kraj": {"cs": "Kraj", "en": "Region"},
    "filters.sources": {"cs": "Zdroje", "en": "Sources"},
    "filters.sort": {"cs": "Řazení", "en": "Sort"},
    "sort.relevance": {"cs": "Nejrelevantnější", "en": "Most relevant"},
    "sort.date": {"cs": "Nejnověji vložené", "en": "Newest ads"},
    "car.posted": {"cs": "Vloženo", "en": "Posted"},
    "car.open": {"cs": "Otevřít původní inzerát", "en": "Open the original ad"},
    "car.full_text": {"cs": "Celý popis", "en": "Full description"},
    "car.new": {"cs": "Nové – našlo ho poslední hledání", "en": "New: found by the last search"},
    "car.no_text": {
        "cs": "Inzerát nemá popis. Celý text najdete v původním inzerátu.",
        "en": "The ad has no description. See the original ad for the whole text.",
    },
    "car.close": {"cs": "Zavřít", "en": "Close"},
    "car.unchecked": {
        "cs": "Ověřuji, zda je stále v prodeji…",
        "en": "Checking it is still for sale…",
    },
    "page.top": {"cs": "Nahoru", "en": "Back to top"},
    "car.hide": {"cs": "Skrýt", "en": "Hide"},
    "car.hide_hint": {"cs": "Skrýt auto ze seznamu", "en": "Hide the car from the list"},
    "hidden.count": {"cs": "Skryto: {n}", "en": "Hidden: {n}"},
    "hidden.restore_last": {"cs": "Vrátit poslední", "en": "Show the last one"},
    "hidden.restore_all": {"cs": "Vrátit všechny", "en": "Show all"},
    "scrape.button": {"cs": "Obnovit nabídky", "en": "Refresh offers"},
    "scrape.button_hint": {
        "cs": "Ověří, zda auta v seznamu jsou stále v prodeji, a doplní nová nad ně.",
        "en": "Checks the listed cars are still for sale and adds new ones on top.",
    },
    "scrape.check": {
        "cs": "Ověřuji, zda auta jsou stále v prodeji: {n}",
        "en": "Checking the cars are still for sale: {n}",
    },
    "scrape.restarting": {
        "cs": "Zastavuji hledání a začínám od začátku…",
        "en": "Stopping the search to start over…",
    },
    "scrape.geocode": {"cs": "Určuji polohu aut…", "en": "Placing the cars on the map…"},
    "scrape.addresses": {
        "cs": "Stahuji adresy obcí z RÚIAN (jednou za měsíc, asi 63 MB): {mb} MB…",
        "en": "Downloading the addresses of towns from RÚIAN (once a month, about 63 MB): {mb} MB…",
    },
    "scrape.listing": {"cs": "Procházím {source}: {n} aut", "en": "Reading {source}: {n} cars"},
    "scrape.parallel": {"cs": "Procházím portály:", "en": "Browsing portals:"},
    "scrape.details": {
        "cs": "Čtu podrobnosti inzerátů na {source}: {n}",
        "en": "Reading ad details on {source}: {n}",
    },
    "scrape.merged": {
        "cs": "Sloučeno s inzerátem z jiného serveru: {n}.",
        "en": "Merged with an ad on another site: {n}.",
    },
    "scrape.partial": {
        "cs": "Některé zdroje se nepodařilo načíst: {sources}.",
        "en": "Some sources could not be read: {sources}.",
    },
    "scrape.start": {"cs": "Spouštím…", "en": "Starting…"},
    "scrape.clear": {"cs": "Mažu dříve nalezená auta…", "en": "Deleting the cars found before…"},
    "scrape.fresh": {"cs": "Obnovit od začátku", "en": "Start over"},
    "scrape.fresh_hint": {
        "cs": "Smaže všechna nalezená auta a najde je znovu.",
        "en": "Deletes every car found and finds them again.",
    },
    "scrape.fresh_confirm": {
        "cs": "Smazat všechna nalezená auta, i skrytá, a hledat znovu?",
        "en": "Delete every car found, hidden ones too, and search again?",
    },
    "scrape.done": {
        "cs": "Hotovo: {new} nových, {updated} změněných, {removed} již neprodávaných.",
        "en": "Done: {new} new, {updated} changed, {removed} no longer for sale.",
    },
    "scrape.failed": {"cs": "Stahování selhalo: {msg}", "en": "Download failed: {msg}"},
    "status.last_run": {"cs": "poslední aktualizace {when}", "en": "last updated {when}"},
    "status.auto_off": {"cs": "automatické hledání vypnuto", "en": "automatic checks off"},
    "status.next": {"cs": "další hledání {when}", "en": "next check {when}"},
    "status.paused": {
        "cs": "automatické hledání pozastaveno do {when}",
        "en": "automatic checks paused until {when}",
    },
    "nav.settings": {"cs": "Nastavení", "en": "Settings"},
    "settings.title": {"cs": "Nastavení", "en": "Settings"},
    "settings.saved": {"cs": "Uloženo.", "en": "Saved."},
    "settings.data": {"cs": "Data aplikace", "en": "App data"},
    "settings.data_dir": {"cs": "Složka s daty", "en": "Data folder"},
    "settings.log_dir": {"cs": "Záznamy (logy)", "en": "Logs"},
    "settings.save": {"cs": "Uložit", "en": "Save"},
    "settings.wizard": {"cs": "Spustit průvodce znovu", "en": "Run the setup wizard again"},
    "settings.wizard.hint": {
        "cs": "Projde všechna nastavení krok za krokem.",
        "en": "Goes through all settings step by step.",
    },
    "licenses.title": {"cs": "Licence", "en": "Licenses"},
    "licenses.open": {"cs": "Zobrazit licence", "en": "Show licenses"},
    "licenses.hint": {
        "cs": "Licenční texty programů a knihoven, které AdaCar obsahuje.",
        "en": "License texts of the programs and libraries AdaCar includes.",
    },
    "licenses.intro": {
        "cs": "AdaCar obsahuje tyto programy a knihovny. Kliknutím zobrazíte licenční text.",
        "en": "AdaCar includes these programs and libraries. Click one to read its license.",
    },
    "licenses.group.notices": {"cs": "Přehled", "en": "Overview"},
    "licenses.group.programs": {"cs": "Programy", "en": "Programs"},
    "licenses.group.runtime": {"cs": "Python", "en": "Python"},
    "licenses.group.python": {"cs": "Balíčky Pythonu", "en": "Python packages"},
    "licenses.group.web": {"cs": "Webové knihovny", "en": "Web libraries"},
    "licenses.none": {"cs": "Licenční texty nebyly nalezeny.", "en": "No license texts found."},
    "settings.appearance": {"cs": "Jazyk a vzhled", "en": "Language and appearance"},
    "settings.instant": {"cs": "Změny se ukládají hned.", "en": "Changes are saved at once."},
    "settings.profile": {"cs": "Odkud vyrážím", "en": "Where I start from"},
    "settings.cars": {"cs": "Modely a cena", "en": "Models and price"},
    "settings.cars.summary": {"cs": "{makes} · {price}", "en": "{makes} · {price}"},
    "settings.cars.all": {"cs": "všechny značky", "en": "all brands"},
    "settings.cars.any_price": {"cs": "jakákoli cena", "en": "any price"},
    "settings.cars.price": {"cs": "do {price} Kč", "en": "up to {price} CZK"},
    "settings.profile.summary": {
        "cs": "{home} · {mode} · {limit}",
        "en": "{home} · {mode} · {limit}",
    },
    "settings.profile.limit": {"cs": "nejvýš {d}", "en": "up to {d}"},
    "settings.no_home": {"cs": "domov nezadán", "en": "no home set"},
    "settings.sources.summary": {
        "cs": "zapnuto {on} z {total}",
        "en": "{on} of {total} switched on",
    },
    "settings.off": {"cs": "vypnuto", "en": "off"},
    "auto.title": {"cs": "Automatické hledání", "en": "Automatic checks"},
    "auto.enabled": {"cs": "Automaticky hledat nová auta", "en": "Look for new cars automatically"},
    "auto.interval": {"cs": "Jak často", "en": "How often"},
    "auto.every": {"cs": "každých {h} h", "en": "every {h} h"},
    "auto.start_with_windows": {
        "cs": "Spouštět se systémem Windows (v oznamovací oblasti)",
        "en": "Start with Windows (in the notification area)",
    },
    "auto.notifications": {"cs": "Upozornit mě na nová auta", "en": "Tell me about new cars"},
    "auto.hint": {
        "cs": "Aplikace pak běží i se zavřeným oknem, ikonou vpravo dole u hodin. S vypnutým "
        "automatickým hledáním hledá aplikace jen po kliknutí na „Obnovit nabídky“.",
        "en": "The app then keeps running with its window closed, as an icon next to the "
        "clock. With automatic searching off, the app searches only when you click "
        "“Refresh offers”.",
    },
    "auto.save": {"cs": "Uložit", "en": "Save"},
    "update.title": {"cs": "Aktualizace", "en": "Updates"},
    "update.version": {
        "cs": "Nainstalovaná verze: {version}",
        "en": "Installed version: {version}",
    },
    "update.auto": {
        "cs": "Instalovat aktualizace automaticky",
        "en": "Install updates automatically",
    },
    "update.auto_on": {"cs": "automaticky", "en": "automatic"},
    "update.auto_hint": {
        "cs": "Jednou denně se podívá na github.com/Gig4but/AdaCar. Novou verzi stáhne, ověří "
        "její kontrolní součet a nainstaluje ji při příštím spuštění.",
        "en": "Looks at github.com/Gig4but/AdaCar once a day. A new version is downloaded, "
        "its checksum verified, and it is installed at the next start.",
    },
    "update.check": {"cs": "Zkontrolovat aktualizace", "en": "Check for updates"},
    "update.install": {"cs": "Nainstalovat", "en": "Install"},
    "update.checking": {"cs": "Hledám novou verzi…", "en": "Looking for a new version…"},
    "update.current": {"cs": "Máte nejnovější verzi.", "en": "You have the latest version."},
    "update.available": {
        "cs": "Je k dispozici verze {version}.",
        "en": "Version {version} is available.",
    },
    "update.downloading": {
        "cs": "Stahuji verzi {version}: {percent} %",
        "en": "Downloading version {version}: {percent} %",
    },
    "update.ready": {
        "cs": "Verze {version} je stažená a nainstaluje se při příštím spuštění.",
        "en": "Version {version} is downloaded and is installed at the next start.",
    },
    "update.installing": {
        "cs": "AdaCar se zavře, nainstaluje verzi {version} a znovu se spustí.",
        "en": "AdaCar closes, installs version {version} and starts again.",
    },
    "update.failed": {"cs": "Aktualizace se nepovedla: {msg}", "en": "The update failed: {msg}"},
    "update.dev": {
        "cs": "Aktualizovat se dá jen nainstalovaná aplikace.",
        "en": "Only the installed app can update itself.",
    },
    "auto.pause": {"cs": "Pozastavit na 24 h", "en": "Pause for 24 h"},
    "auto.resume": {"cs": "Pokračovat", "en": "Resume"},
    "wizard.auto_on": {
        "cs": "Automatické hledání je zapnuté: každých {h} h.",
        "en": "Automatic checks are on: every {h} h.",
    },
    "tray.open": {"cs": "Otevřít", "en": "Open"},
    "tray.auto": {"cs": "Automatické hledání", "en": "Automatic checks"},
    "tray.pause": {"cs": "Pozastavit na 24 h", "en": "Pause for 24 h"},
    "tray.resume": {"cs": "Pokračovat v automatickém hledání", "en": "Resume automatic checks"},
    "tray.quit": {"cs": "Ukončit", "en": "Quit"},
    "notify.new": {"cs": "Nová auta: {n}", "en": "New cars: {n}"},
    "notify.saved": {"cs": "{name}: {what}", "en": "{name}: {what}"},
    "notify.new_part": {"cs": "nová auta {n}", "en": "{n} new cars"},
    "notify.cheaper_part": {"cs": "zlevněná {n}", "en": "{n} cheaper"},
    "notify.visit_cheaper": {
        "cs": "Zlevněné auto na prohlídku: {title} za {price}",
        "en": "A car to view got cheaper: {title} for {price}",
    },
    "export.label": {"cs": "Stáhnout seznam", "en": "Download the list"},
    "export.title": {"cs": "Inzerát", "en": "Ad"},
    "export.make": {"cs": "Značka", "en": "Make"},
    "export.model": {"cs": "Model", "en": "Model"},
    "export.year": {"cs": "Rok", "en": "Year"},
    "export.km": {"cs": "Najeto (km)", "en": "Mileage (km)"},
    "export.price": {"cs": "Cena", "en": "Price"},
    "export.price_was": {"cs": "Cena před zlevněním", "en": "Price before the drop"},
    "export.currency": {"cs": "Měna", "en": "Currency"},
    "export.fuel": {"cs": "Palivo", "en": "Fuel"},
    "export.gearbox": {"cs": "Převodovka", "en": "Gearbox"},
    "export.power": {"cs": "Výkon (kW)", "en": "Power (kW)"},
    "export.body": {"cs": "Karoserie", "en": "Body"},
    "export.place": {"cs": "Místo", "en": "Place"},
    "export.kraj": {"cs": "Kraj", "en": "Region"},
    "export.seller": {"cs": "Prodejce", "en": "Seller"},
    "export.vin": {"cs": "VIN", "en": "VIN"},
    "export.posted": {"cs": "Vloženo", "en": "Posted"},
    "export.sources": {"cs": "Zdroje", "en": "Sources"},
    "export.link": {"cs": "Odkaz", "en": "Link"},
    "saved.title": {"cs": "Uložená hledání", "en": "Saved searches"},
    "saved.save": {"cs": "Uložit hledání", "en": "Save search"},
    "saved.prompt": {"cs": "Název hledání", "en": "Name of the search"},
    "saved.default_name": {"cs": "Moje hledání", "en": "My search"},
    "saved.new": {"cs": "nové: {n}", "en": "new: {n}"},
    "saved.none": {
        "cs": "Zatím žádná. Nastavte filtry a klikněte na „Uložit hledání“.",
        "en": "None yet. Set the filters and click “Save search”.",
    },
    "saved.notify_on": {
        "cs": "Upozorňovat na nová auta (zapnuto)",
        "en": "Tell me about new cars (on)",
    },
    "saved.notify_off": {
        "cs": "Upozorňovat na nová auta (vypnuto)",
        "en": "Tell me about new cars (off)",
    },
    "saved.delete": {"cs": "Smazat", "en": "Delete"},
    "saved.full": {
        "cs": "Uložit lze nejvýš {n} hledání.",
        "en": "At most {n} searches can be saved.",
    },
    "sort.travel": {"cs": "Nejkratší cesta", "en": "Shortest trip"},
    "wizard.depart": {"cs": "Obvyklý odjezd na prohlídku", "en": "Usual departure to a viewing"},
    "wizard.depart.hint": {
        "cs": "Ve všední den; podle něj se počítají spoje MHD a vlaků.",
        "en": "On a weekday; public transport connections are computed for this time.",
    },
    "commute.title": {"cs": "Jak se tam dostanu", "en": "How to get there"},
    "commute.open": {"cs": "Jak se tam dostanu", "en": "How to get there"},
    "commute.close": {"cs": "Zavřít", "en": "Close"},
    "commute.from": {"cs": "Odkud pojedete", "en": "Travelling from"},
    "commute.mode.any": {"cs": "Čímkoli", "en": "Any way"},
    "commute.mode.car": {"cs": "Autem", "en": "By car"},
    "commute.mode.transit": {"cs": "MHD a vlakem", "en": "By public transport"},
    "commute.mode.walk": {"cs": "Pěšky", "en": "On foot"},
    "commute.mode.bike": {"cs": "Na kole", "en": "By bike"},
    "commute.within": {"cs": "do", "en": "within"},
    "commute.no_limit": {"cs": "bez omezení", "en": "no limit"},
    "commute.depart": {"cs": "Odjezd", "en": "Departure"},
    "commute.no_start": {
        "cs": "Zadejte, odkud pojedete, a uvidíte dobu cesty k autům.",
        "en": "Enter where you travel from to see the trip to each car.",
    },
    "commute.estimate": {
        "cs": "Doba cesty je zatím odhad",
        "en": "Travel times are estimates for now",
    },
    "commute.estimate_short": {"cs": "odhad", "en": "estimate"},
    "commute.no_map": {
        "cs": "Doba cesty je odhad vzdušnou čarou; přesné časy a trasy dá offline mapa.",
        "en": "Travel times are straight-line estimates; the offline map makes them exact.",
    },
    "commute.computing": {
        "cs": "Počítám přesné doby cesty…",
        "en": "Computing exact travel times…",
    },
    "commute.too_far": {"cs": "dál než {d}", "en": "more than {d}"},
    "commute.unknown": {
        "cs": "Toto místo neznám. Zkuste název obce.",
        "en": "Unknown place. Try the name of a town.",
    },
    "commute.missing_start": {
        "cs": "Nejdřív zadejte, odkud pojedete (pole nad seznamem).",
        "en": "First enter where you travel from (the field above the list).",
    },
    "commute.missing_place": {
        "cs": "U tohoto auta není známé místo, takže cestu nelze spočítat.",
        "en": "This car has no known place, so the trip cannot be computed.",
    },
    "commute.depart_or_arrive": {"cs": "Odjezd nebo příjezd", "en": "Departure or arrival"},
    "commute.leave_at": {"cs": "Odjezd v", "en": "Leave at"},
    "commute.arrive_by": {"cs": "Příjezd do", "en": "Arrive by"},
    "commute.day": {"cs": "Den prohlídky", "en": "Day of the viewing"},
    "commute.day.workday": {"cs": "nejbližší pracovní den", "en": "next working day"},
    "commute.day.sat": {"cs": "sobota", "en": "Saturday"},
    "commute.day.sun": {"cs": "neděle", "en": "Sunday"},
    "commute.back_by_car": {
        "cs": "Zpátky koupeným autem asi {d}.",
        "en": "Back in the car you buy: about {d}.",
    },
    "commute.transfers": {"cs": "přestupy: {n}", "en": "transfers: {n}"},
    "commute.walk_leg": {"cs": "pěšky {m} min", "en": "walk {m} min"},
    "commute.towards": {"cs": "směr", "en": "towards"},
    "commute.failed": {
        "cs": "Trasu se nepodařilo spočítat. Zkuste to za chvíli znovu.",
        "en": "The route could not be computed. Try again in a moment.",
    },
    "commute.no_route": {
        "cs": "V tuto dobu nenacházím žádné spojení.",
        "en": "No connection found at this time.",
    },
    "commute.estimate_minutes": {"cs": "Odhadem asi {d}.", "en": "Roughly {d}."},
    "commute.estimate_why": {
        "cs": "Přesné trasy budou k dispozici, až se připraví mapy a jízdní řády.",
        "en": "Exact routes will be available once the maps and timetables are ready.",
    },
    "commute.open_in": {"cs": "Otevřít v:", "en": "Open in:"},
    "commute.area": {"cs": "Kam se dostanu za", "en": "Where I can get within"},
    "commute.start_here": {"cs": "Vyrážet odsud", "en": "Start from here"},
    "commute.attribution": {
        "cs": "Jízdní řády: Spojenka (CIS JŘ a další, jen pro nekomerční použití). Mapa: © "
        "přispěvatelé OpenStreetMap. Výpočet tras: MOTIS.",
        "en": "Timetables: Spojenka (CIS JŘ and others, non-commercial use only). Map: © "
        "OpenStreetMap contributors. Routing: MOTIS.",
    },
    "visit.add": {"cs": "Na prohlídku", "en": "Add to viewings"},
    "visit.marked": {"cs": "V prohlídkách", "en": "In viewings"},
    "visit.hint": {
        "cs": "Auta na prohlídku seřadím do jedné cesty autem, s nejkratší jízdou.",
        "en": "Cars to view are put in one drive, in the order with the least driving.",
    },
    "visit.open": {"cs": "Prohlídky ({n})", "en": "Viewings ({n})"},
    "visit.title": {"cs": "Cesta na prohlídky", "en": "Viewing trip"},
    "visit.intro": {
        "cs": "Z místa {origin} ke všem autům a zpět, v pořadí s nejkratší jízdou autem.",
        "en": "From {origin} to every car and back, in the order with the least driving.",
    },
    "visit.none": {
        "cs": "Zatím tu žádné auto není. Přidejte ho v okně „Jak se tam dostanu“ tlačítkem "
        "„Na prohlídku“.",
        "en": 'No car yet. Add one in the "How to get there" window with "Add to viewings".',
    },
    "visit.back": {"cs": "Zpátky: {d}", "en": "Back: {d}"},
    "visit.total": {
        "cs": "Za volantem celkem {d} (bez času na prohlídky)",
        "en": "{d} at the wheel in all (viewings not counted)",
    },
    "visit.unplaced": {
        "cs": "U těchto aut neznám místo, v trase nejsou:",
        "en": "These cars have no known place and are not on the route:",
    },
    "visit.too_many": {
        "cs": "Na jednu cestu nejvýš {n} aut; tato zbývají na další:",
        "en": "At most {n} cars per trip; these are left for another:",
    },
    "visit.remove": {"cs": "Odebrat", "en": "Remove"},
    "visit.ended": {"cs": "inzerát skončil", "en": "ad ended"},
    "routing.title": {"cs": "Mapy a jízdní řády", "en": "Maps and timetables"},
    "routing.step.download_osm": {"cs": "Stahuji mapu", "en": "Downloading the map"},
    "routing.step.download_gtfs": {"cs": "Stahuji jízdní řády", "en": "Downloading timetables"},
    "routing.step.clip": {"cs": "Vyřezávám okolí domova", "en": "Cutting out the home area"},
    "routing.step.timetable": {"cs": "Připravuji jízdní řády", "en": "Preparing timetables"},
    "routing.step.import": {"cs": "Připravuji vyhledávání tras", "en": "Preparing route search"},
    "routing.not_ready": {
        "cs": "Zatím nejsou připravené; doba cesty bude k dispozici, až budou.",
        "en": "Not ready yet; travel times will be available once they are.",
    },
    "routing.not_downloaded": {"cs": "Nestaženo", "en": "Not downloaded"},
    "routing.download": {"cs": "Stáhnout mapu", "en": "Download the map"},
    "routing.download.text": {
        "cs": "Doba cesty k prodejci, filtr podle dojezdu a trasy potřebují offline mapu a "
        "jízdní řády. Stáhne se asi 1 GB a pak se na pozadí několik minut připravují; "
        "aplikaci můžete mezitím používat. Bez mapy je doba cesty jen odhad vzdušnou "
        "čarou.",
        "en": "The trip to a seller, the travel-time filter and routes need an offline map "
        "and timetables. About 1 GB is downloaded and then prepared in the background "
        "for a few minutes; you can use the app meanwhile. Without the map, trips are "
        "straight-line estimates.",
    },
    "routing.needs_home": {
        "cs": "Pro okolí domova nejdřív zadejte, odkud vyrážíte (Co hledám).",
        "en": "For the area around home, first enter where you start from (What I look for).",
    },
    "routing.ready": {"cs": "Připraveno", "en": "Ready"},
    "routing.failed": {"cs": "Poslední aktualizace selhala", "en": "The last update failed"},
    "routing.area": {"cs": "Oblast", "en": "Area"},
    "routing.area.auto": {"cs": "Podle počítače", "en": "Depending on this PC"},
    "routing.area.whole": {"cs": "celá Česká republika", "en": "all of Czechia"},
    "routing.area.home": {"cs": "okolí domova (150 km)", "en": "home area (150 km)"},
    "routing.area.home_km": {"cs": "okolí domova ({km} km)", "en": "home area ({km} km)"},
    "routing.area.hint": {
        "cs": "Celá republika potřebuje asi 6 GB paměti při přípravě a 5 GB na disku; okolí "
        "domova jen zlomek.",
        "en": "All of Czechia needs about 6 GB of memory to prepare and 5 GB of disk; the "
        "home area only a fraction.",
    },
    "routing.update_now": {"cs": "Aktualizovat teď", "en": "Update now"},
    "routing.osm_at": {"cs": "Mapa stažena", "en": "Map downloaded"},
    "routing.gtfs_at": {"cs": "Jízdní řády staženy", "en": "Timetables downloaded"},
    "routing.imported_at": {"cs": "Připraveno", "en": "Prepared"},
    "wizard.makes": {"cs": "Značky (nepovinné)", "en": "Brands (optional)"},
    "wizard.makes.hint": {
        "cs": "Nic nevybráno = všechny značky. Vybrané značky stáhneme a předvyplníme do hledání.",
        "en": "None chosen = every brand. The chosen brands are downloaded and preset in the "
        "search.",
    },
    "wizard.max_price": {
        "cs": "Nejvyšší cena v Kč (nepovinné)",
        "en": "Highest price in CZK (optional)",
    },
    "sources.sauto.warning": {
        "cs": "Sauto.cz automatické čtení svých stránek v souboru robots.txt zakazuje "
        "(povoluje ho jen vyhledávačům). Aplikace čte jen veřejné inzeráty bez "
        "přihlášení, pomalu (jeden dotaz za 3 sekundy) a při prvním odmítnutí na 12 "
        "hodin přestane. Sauto.cz ale může váš počítač (IP adresu) na čas zablokovat.",
        "en": "Sauto.cz forbids automatic reading of its pages in its robots.txt file (it "
        "allows only search engines). The app reads only public ads without logging in, "
        "slowly (one request every 3 seconds), and stops for 12 hours at the first "
        "refusal. Sauto.cz may still block your computer (IP address) for a while.",
    },
    "filters.make": {"cs": "Značka", "en": "Brand"},
    "filters.fuel": {"cs": "Palivo", "en": "Fuel"},
    "filters.gearbox": {"cs": "Převodovka", "en": "Gearbox"},
    "filters.body": {"cs": "Karoserie", "en": "Body"},
    "filters.drive": {"cs": "Pohon", "en": "Drive"},
    "filters.seller_kind": {"cs": "Prodejce", "en": "Seller"},
    "filters.price": {"cs": "Cena (Kč)", "en": "Price (CZK)"},
    "filters.year": {"cs": "Rok výroby", "en": "Year"},
    "filters.from": {"cs": "od", "en": "from"},
    "filters.to": {"cs": "do", "en": "to"},
    "filters.km_max": {"cs": "Najeto nejvýš (km)", "en": "Mileage at most (km)"},
    "filters.power_min": {"cs": "Výkon alespoň (kW)", "en": "Power at least (kW)"},
    "filters.with_price": {"cs": "Jen s uvedenou cenou", "en": "Only with a price"},
    "filters.with_photo": {"cs": "Jen s fotkou", "en": "Only with a photo"},
    "filters.apply": {"cs": "Použít", "en": "Apply"},
    "sort.price": {"cs": "Nejlevnější", "en": "Cheapest"},
    "sort.price_desc": {"cs": "Nejdražší", "en": "Most expensive"},
    "sort.km": {"cs": "Nejméně najeto", "en": "Lowest mileage"},
    "sort.year": {"cs": "Nejmladší", "en": "Newest model year"},
    "car.price_none": {"cs": "Cena dohodou", "en": "Price on request"},
    "car.vat": {"cs": "lze odečíst DPH", "en": "VAT deductible"},
    "car.photo": {"cs": "Fotka auta", "en": "Photo of the car"},
    "car.specs": {"cs": "Parametry", "en": "Specifications"},
    "car.equipment": {"cs": "Výbava", "en": "Equipment"},
    "car.year": {"cs": "Rok výroby", "en": "Year"},
    "car.mileage": {"cs": "Najeto", "en": "Mileage"},
    "car.engine": {"cs": "Motor", "en": "Engine"},
    "car.power": {"cs": "Výkon", "en": "Power"},
    "car.fuel": {"cs": "Palivo", "en": "Fuel"},
    "car.gearbox": {"cs": "Převodovka", "en": "Gearbox"},
    "car.body": {"cs": "Karoserie", "en": "Body"},
    "car.drive": {"cs": "Pohon", "en": "Drive"},
    "car.condition": {"cs": "Stav", "en": "Condition"},
    "car.color": {"cs": "Barva", "en": "Colour"},
    "car.doors_seats": {"cs": "Dveře / místa", "en": "Doors / seats"},
    "car.origin": {"cs": "Země původu", "en": "Country of origin"},
    "car.vin": {"cs": "VIN", "en": "VIN"},
    "car.seller": {"cs": "Prodejce", "en": "Seller"},
    "car.history": {"cs": "Historie", "en": "History"},
    "car.first_owner": {"cs": "1. majitel", "en": "First owner"},
    "car.service_book": {"cs": "servisní knížka", "en": "service book"},
    "car.crashed": {"cs": "havarováno", "en": "previously damaged"},
    "car.not_crashed": {"cs": "nehavarováno", "en": "not damaged"},
    "car.price_differs": {"cs": "Jinde za {price}", "en": "Elsewhere for {price}"},
    "car.cheaper": {"cs": "zlevněno z {price}", "en": "down from {price}"},
    "car.cheaper_on": {"cs": "Zlevněno {date}", "en": "Price dropped on {date}"},
    "car.price_history": {"cs": "Vývoj ceny", "en": "Price history"},
    "status.cars": {"cs": "Auta v prodeji: {n}", "en": "Cars for sale: {n}"},
    # --- AI (PLAN §6) ---
    "nav.add": {"cs": "Vložit inzerát", "en": "Add an ad"},
    "add.title": {"cs": "Vložit inzerát", "en": "Add an ad"},
    "add.heading": {
        "cs": "Přidejte auto, které jste našli jinde",
        "en": "Add a car you found elsewhere",
    },
    "add.text": {
        "cs": "Z Facebooku, novin nebo zprávy: vložte text inzerátu, nebo jeho snímek obrazovky."
        " Umělá inteligence z něj doplní značku, model, rok, najeté km a další údaje.",
        "en": "From Facebook, a newspaper or a message: paste the ad's text, or add a"
        " screenshot of it. The AI fills in the make, model, year, mileage and more.",
    },
    "add.ad_text": {"cs": "Text inzerátu", "en": "Ad text"},
    "add.placeholder": {
        "cs": "Např. Prodám Škoda Octavia combi 2.0 TDI, r. v. 2017, 186 tis. km, 289 000 Kč…",
        "en": "E.g. Škoda Octavia estate 2.0 TDI, 2017, 186,000 km, 289,000 CZK…",
    },
    "add.picture": {"cs": "Snímek obrazovky nebo fotka", "en": "Screenshot or photo"},
    "add.picture_hint": {
        "cs": "Text z obrázku přečte AI, když je model stažený.",
        "en": "The AI reads the text in the picture once a model is downloaded.",
    },
    "add.link": {"cs": "Odkaz (nepovinný)", "en": "Link (optional)"},
    "add.save": {"cs": "Uložit inzerát", "en": "Save the ad"},
    "add.saved": {
        "cs": "Uloženo. Chybějící údaje doplní AI na pozadí.",
        "en": "Saved. The AI fills in missing details in the background.",
    },
    "add.another": {"cs": "Vložit další", "en": "Add another"},
    "add.picture_title": {"cs": "Inzerát z obrázku", "en": "Ad from a picture"},
    "add.empty": {
        "cs": "Vložte text inzerátu nebo obrázek.",
        "en": "Paste the ad text or add a picture.",
    },
    "add.too_big": {"cs": "Obrázek je větší než 15 MB.", "en": "The picture is larger than 15 MB."},
    "add.not_picture": {
        "cs": "Soubor není obrázek (JPEG, PNG…).",
        "en": "The file is not a picture (JPEG, PNG…).",
    },
    "wizard.ai": {
        "cs": "Má umělá inteligence číst texty inzerátů?",
        "en": "Should the AI read the ad texts?",
    },
    "ai.title": {"cs": "Umělá inteligence", "en": "Artificial intelligence"},
    "ai.text": {
        "cs": "Model umělé inteligence běží jen na vašem počítači, nic se nikam neposílá."
        " Doplní značku, model, rok, najeté km a další údaje, které inzerát uvádí jen v textu,"
        " a upozorní na podezřelé inzeráty. Model Výborný navíc dá jako první fotku auta"
        " zepředu, když inzerát začíná logem nebo interiérem.",
        "en": "The AI model runs only on your computer; nothing is sent anywhere. It fills in"
        " the make, model, year, mileage and other details an ad gives only in its text,"
        " and flags suspicious ads. The Excellent model also puts a photo of the car from the"
        " front first when an ad opens with a logo or the interior.",
    },
    "ai.hardware": {
        "cs": "Tento počítač: {ram} GB RAM · {gpu} · {disk} GB volného místa",
        "en": "This computer: {ram} GB RAM · {gpu} · {disk} GB free disk space",
    },
    "ai.no_gpu": {"cs": "bez grafické karty", "en": "no graphics card"},
    "ai.auto": {
        "cs": "Automaticky – nejlepší pro tento počítač",
        "en": "Automatic – best for this computer",
    },
    "ai.auto.now": {"cs": "teď vybráno: {tier}", "en": "currently: {tier}"},
    "ai.auto.none": {"cs": "teď: jen pravidla", "en": "currently: rules only"},
    "ai.tier.simple": {"cs": "Jednoduchý", "en": "Simple"},
    "ai.tier.good": {"cs": "Dobrý", "en": "Good"},
    "ai.tier.excellent": {"cs": "Výborný", "en": "Excellent"},
    "ai.recommended": {"cs": "Doporučeno", "en": "Recommended"},
    "ai.fit.green": {"cs": "Poběží plynule", "en": "Runs smoothly"},
    "ai.fit.yellow": {"cs": "Poběží, ale pomaleji", "en": "Works, but slower"},
    "ai.fit.red": {"cs": "Nedoporučeno", "en": "Not recommended"},
    "ai.seconds": {"cs": "~{s} s na inzerát", "en": "~{s} s per ad"},
    "ai.measured": {"cs": "změřeno", "en": "measured"},
    "ai.downloaded": {"cs": "staženo", "en": "downloaded"},
    "ai.why.ram": {"cs": "málo paměti", "en": "not enough memory"},
    "ai.why.ram_tight": {"cs": "paměti je těsně", "en": "memory is tight"},
    "ai.why.slow": {"cs": "příliš pomalé", "en": "too slow"},
    "ai.why.slower": {"cs": "pomalejší", "en": "slower"},
    "ai.why.disk": {"cs": "málo místa na disku", "en": "not enough disk space"},
    "ai.legend.green": {
        "cs": "Poběží plynule: do 1 s na inzerát.",
        "en": "Runs smoothly: up to 1 s per ad.",
    },
    "ai.legend.yellow": {
        "cs": "Poběží, ale pomaleji: 1–4 s na inzerát, přečtení všech inzerátů bude trvat dlouho.",
        "en": "Works, but slower: 1–4 s per ad; reading all the ads will take a long time.",
    },
    "ai.legend.red": {
        "cs": "Nedoporučeno: přes 4 s na inzerát, nebo málo paměti či místa na disku."
        " Doporučujeme umělou inteligenci vypnout.",
        "en": "Not recommended: over 4 s per ad, or not enough memory or disk space."
        " We recommend turning the AI off.",
    },
    "ai.server": {"cs": "Vlastní server", "en": "Own server"},
    "ai.off": {"cs": "Bez umělé inteligence (jen pravidla)", "en": "No AI (rules only)"},
    "ai.red_confirm": {
        "cs": "Tento model je pro váš počítač příliš náročný. Opravdu ho chcete?",
        "en": "This model is too demanding for your computer. Do you really want it?",
    },
    "ai.reason.igpu": {
        "cs": "{ram} GB RAM, integrovaná grafika → {tier}",
        "en": "{ram} GB RAM, integrated graphics → {tier}",
    },
    "ai.reason.dgpu": {
        "cs": "{ram} GB RAM, grafická karta s {vram} GB → {tier}",
        "en": "{ram} GB RAM, graphics card with {vram} GB → {tier}",
    },
    "ai.reason.cpu": {
        "cs": "{ram} GB RAM bez grafické karty → {tier}",
        "en": "{ram} GB RAM, no graphics card → {tier}",
    },
    "ai.reason.none": {
        "cs": "Žádný model na tomto počítači nepoběží dobře (málo paměti nebo příliš pomalé);"
        " aplikace použije jen pravidla.",
        "en": "No model runs well on this computer (not enough memory, or too slow);"
        " the app uses rules only.",
    },
    "ai.download.downloading": {
        "cs": "Stahuji model {model}: {percent} % ({done} z {total} GB)",
        "en": "Downloading {model}: {percent} % ({done} of {total} GB)",
    },
    "ai.download.benchmark": {
        "cs": "Měřím rychlost modelu na tomto počítači…",
        "en": "Measuring the model's speed on this computer…",
    },
    "ai.download.failed": {
        "cs": "Stažení se nepovedlo: {msg}. Zkusím to znovu při příštím spuštění.",
        "en": "The download failed: {msg}. It will be retried at the next start.",
    },
    "ai.ready": {"cs": "Model {model} je připraven.", "en": "Model {model} is ready."},
    "ai.not_ready": {
        "cs": "Model zatím není stažený; aplikace používá jen pravidla.",
        "en": "No model is downloaded yet; the app uses rules only.",
    },
    "ai.notice.upgraded": {
        "cs": "Model byl vylepšen na {model}.",
        "en": "The model was upgraded to {model}.",
    },
    "ai.busy.no_model": {"cs": "model není stažený", "en": "no model downloaded"},
    "ai.busy.pc_busy": {"cs": "počítač je právě vytížený", "en": "the computer is busy right now"},
    "ai.busy.memory": {"cs": "málo volné paměti", "en": "not enough free memory"},
    "ai.enrich.pictures": {
        "cs": "AI čte vložené obrázky: {done} z {total}",
        "en": "AI is reading added pictures: {done} of {total}",
    },
    "ai.enrich.ai": {
        "cs": "AI doplňuje údaje: {done} z {total} inzerátů",
        "en": "AI is filling in details: {done} of {total} ads",
    },
    "ai.enrich.covers": {
        "cs": "AI vybírá fotky aut zepředu: {done} z {total}",
        "en": "AI is picking front photos of the cars: {done} of {total}",
    },
    "ai.enrich.waiting": {
        "cs": "AI počká ({reason}), pak bude pokračovat",
        "en": "AI is waiting ({reason}) and will continue",
    },
    "ai.enrich.failed": {"cs": "AI se zastavila: {msg}", "en": "The AI stopped: {msg}"},
    "ai.advanced": {"cs": "Pokročilé", "en": "Advanced"},
    "ai.model_choice": {"cs": "Konkrétní model", "en": "Specific model"},
    "ai.server_url": {
        "cs": "Vlastní server (např. Ollama: http://127.0.0.1:11434)",
        "en": "Own server (e.g. Ollama: http://127.0.0.1:11434)",
    },
    "ai.server_model": {"cs": "Název modelu na serveru", "en": "Model name on the server"},
    "ai.test": {"cs": "Vyzkoušet spojení", "en": "Test connection"},
    "ai.test.ok": {
        "cs": "Server odpovídá. Modely: {models}",
        "en": "The server answers. Models: {models}",
    },
    "ai.test.failed": {"cs": "Server neodpovídá: {msg}", "en": "The server does not answer: {msg}"},
    "ai.test.bad_url": {
        "cs": "Adresa musí začínat http:// nebo https://",
        "en": "The address must start with http:// or https://",
    },
    "ai.models": {"cs": "Stažené modely", "en": "Downloaded models"},
    "ai.models.text": {
        "cs": "Modely zůstávají na disku i po změně volby, takže se k nim můžete vrátit."
        " Smazat je můžete jen vy.",
        "en": "Models stay on disk when the choice changes, so you can go back to them."
        " Only you can delete them.",
    },
    "ai.in_use": {"cs": "používá se", "en": "in use"},
    "ai.delete": {"cs": "Smazat", "en": "Delete"},
    "ai.delete_confirm": {
        "cs": "Smazat tento model z disku? Kdykoli ho můžete stáhnout znovu.",
        "en": "Delete this model from disk? You can download it again any time.",
    },
    "ai.delete_confirm_active": {
        "cs": "Tento model se právě používá. Smazat ho? AI bude stát, dokud se znovu nestáhne"
        " (po uložení volby zde nebo při příštím spuštění).",
        "en": "This model is in use. Delete it? The AI stops until it is downloaded again"
        " (when a choice is saved here, or at the next start).",
    },
    "ai.delete_all": {"cs": "Smazat vše", "en": "Delete all"},
    "ai.delete_all_confirm": {
        "cs": "Smazat všechny stažené modely AI? AI bude stát, dokud se znovu nestáhnou"
        " (po uložení volby zde nebo při příštím spuštění).",
        "en": "Delete all downloaded AI models? The AI stops until they are downloaded again"
        " (when a choice is saved here, or at the next start).",
    },
    "ai.save": {"cs": "Uložit", "en": "Save"},
    "ai.saved": {"cs": "Uloženo.", "en": "Saved."},
    "ai.filled": {
        "cs": "doplnila AI z textu inzerátu",
        "en": "filled in by the AI from the ad text",
    },
    "settings.ai.none": {"cs": "model zatím nestažen", "en": "no model downloaded yet"},
    "ask.placeholder": {
        "cs": "Zeptejte se větou, např. „dieselové kombi do 300 tisíc do hodiny od Kolína“",
        "en": "Ask in a sentence, e.g. “diesel estate under 300k within an hour of Kolín”",
    },
    "ask.button": {"cs": "Zeptat se AI", "en": "Ask AI"},
    "ask.wait.downloading": {
        "cs": "Stahuji AI ({percent} %), pak se tu můžete zeptat větou…",
        "en": "Downloading the AI ({percent} %), then you can ask in a sentence here…",
    },
    "ask.wait.benchmark": {
        "cs": "Zkouším AI na tomto počítači, za chvíli se tu můžete zeptat větou…",
        "en": "Trying the AI on this computer, you can ask here in a moment…",
    },
    "ask.wait.failed": {
        "cs": "Stažení AI se nepovedlo (Nastavení → AI)",
        "en": "The AI download failed (Settings → AI)",
    },
    "ask.wait.off": {"cs": "AI je vypnutá (Nastavení → AI)", "en": "AI is off (Settings → AI)"},
    "ask.wait.none": {
        "cs": "AI zatím není připravená (Nastavení → AI)",
        "en": "The AI is not ready yet (Settings → AI)",
    },
    "ask.failed": {
        "cs": "Dotaz se nepodařilo zpracovat ({msg}). Zkuste běžné hledání.",
        "en": "The question could not be processed ({msg}). Try the normal search.",
    },
    # Warning signs on a car (Car.warnings, ai/enrich.py).
    "warn.title": {"cs": "Pozor", "en": "Watch out"},
    "warn.deposit": {
        "cs": "prodejce chce zálohu předem",
        "en": "the seller wants a deposit up front",
    },
    "warn.abroad": {"cs": "prodejce je v zahraničí", "en": "the seller is abroad"},
    "warn.km_unverified": {
        "cs": "stav km prý nelze zaručit",
        "en": "the mileage is said to be unverified",
    },
    "warn.price_low": {
        "cs": "cena hluboko pod podobnými auty",
        "en": "price far below similar cars",
    },
    "warn.not_offer": {
        "cs": "AI: možná nejde o prodej auta",
        "en": "AI: possibly not a car for sale",
    },
    # Vehicle and seller checks (PLAN §4.5, checks/).
    "warn.vin_make": {"cs": "VIN patří jiné značce", "en": "the VIN belongs to another make"},
    "warn.vin_year": {
        "cs": "VIN ukazuje jiný modelový rok",
        "en": "the VIN shows another model year",
    },
    "warn.km_rollback": {
        "cs": "dříve inzerováno s vyšším stavem km",
        "en": "advertised before with a higher mileage",
    },
    "warn.reg_seized": {
        "cs": "registr: zadržený doklad nebo SPZ",
        "en": "register: papers or plates seized",
    },
    "warn.reg_status": {"cs": "registr: auto není v provozu", "en": "register: car not in use"},
    "warn.reg_make": {"cs": "registr: jiná značka", "en": "register: another make"},
    "warn.reg_year": {
        "cs": "registr: první registrace dříve, než uvádí inzerát",
        "en": "register: first registered earlier than the ad says",
    },
    "warn.reg_power": {"cs": "registr: jiný výkon motoru", "en": "register: another engine power"},
    "warn.reg_imported": {
        "cs": "registr: dovezeno, inzerát píše koupeno v ČR",
        "en": "register: imported, the ad says bought in Czechia",
    },
    "warn.reg_owners": {
        "cs": "registr: více majitelů, inzerát píše 1. majitel",
        "en": "register: more owners, the ad says first owner",
    },
    "warn.dealer_not_found": {
        "cs": "prodejce: IČO není v registru",
        "en": "dealer: IČO not in the register",
    },
    "warn.dealer_ended": {"cs": "prodejce: firma zanikla", "en": "dealer: company dissolved"},
    "warn.dealer_insolvency": {"cs": "prodejce: insolvence", "en": "dealer: insolvency"},
    "warn.dealer_vat": {
        "cs": "prodejce: nespolehlivý plátce DPH",
        "en": "dealer: unreliable VAT payer",
    },
    "checks.title": {"cs": "Prověrky", "en": "Checks"},
    "checks.text": {
        "cs": "AdaCar ověřuje auta podle VIN a prodejce podle obchodního rejstříku. Údaje "
        "z registru vozidel (první registrace, dovoz, počet majitelů, výkon) poskytuje "
        "ministerstvo dopravy jen s vlastním bezplatným klíčem: zaregistrujte se jménem "
        "a e-mailem a klíč sem vložte. Hodnocení prodejců z Google Map potřebuje klíč "
        "Google (1 000 dotazů měsíčně zdarma, AdaCar se zastaví dřív).",
        "en": "AdaCar checks cars by their VIN and dealers in the company register. The "
        "vehicle register's data (first registration, import, owners, power) is given by the "
        "Ministry of Transport only with your own free key: sign up with a name and e-mail "
        "and paste the key here. Dealers' ratings from Google Maps need a Google key (1,000 "
        "free requests a month; AdaCar stops before that).",
    },
    "checks.auto": {"cs": "Prověřovat na pozadí", "en": "Check in the background"},
    "checks.auto.hint": {
        "cs": "Auta z vašeho výběru a jejich prodejci se prověří po každém hledání. Vypnuto: "
        "až po otevření auta.",
        "en": "Cars matching your choice and their dealers are checked after each search. "
        "Off: only when a car is opened.",
    },
    "checks.auto_on": {"cs": "na pozadí", "en": "in the background"},
    "checks.auto_off": {"cs": "po otevření auta", "en": "when a car is opened"},
    "checks.no_keys": {"cs": "bez klíčů", "en": "no keys"},
    "checks.key.register": {"cs": "Klíč k registru vozidel", "en": "Vehicle register key"},
    "checks.key.register.hint": {
        "cs": "Nejvýše 25 dotazů za minutu; dotazuje se jen na auta, která vás zajímají.",
        "en": "At most 25 requests a minute; only cars you are interested in are asked.",
    },
    "checks.key.google": {"cs": "Klíč Google Places", "en": "Google Places key"},
    "checks.key.google.hint": {
        "cs": "Tento měsíc použito {used} z {free} bezplatných dotazů.",
        "en": "Used {used} of {free} free requests this month.",
    },
    "checks.key.get": {"cs": "Jak získat klíč", "en": "How to get a key"},
    "checks.key.saved": {"cs": "uložen (…{end})", "en": "saved (…{end})"},
    "checks.key.remove": {"cs": "Smazat klíč", "en": "Delete the key"},
    "checks.key_invalid": {
        "cs": "Tohle nevypadá jako klíč, nic se neuložilo.",
        "en": "That does not look like a key; nothing was saved.",
    },
    "checks.counts": {
        "cs": "Zatím aut s VIN: {vins}, z registru: {read}; prodejců: {dealers}, s IČO: "
        "{resolved}.",
        "en": "So far: {vins} cars with a VIN, {read} from the register; {dealers} dealers, "
        "{resolved} with an IČO.",
    },
    "checks.sources": {
        "cs": "Zdroje: registr silničních vozidel (dataovozidlech.cz), ARES, insolvenční "
        "rejstřík, registr plátců DPH. Stav tachometru z STK a pátrání po kradených autech "
        "chrání CAPTCHA: u auta jsou odkazy, kde si je ověříte sami.",
        "en": "Sources: the vehicle register (dataovozidlech.cz), ARES, the insolvency "
        "register, the VAT register. Inspection mileage and stolen cars are behind a CAPTCHA: "
        "each car links to where you check them yourself.",
    },
    "checks.register.bad_key": {
        "cs": "Registr vozidel klíč odmítl.",
        "en": "The vehicle register refused the key.",
    },
    "checks.vehicle": {"cs": "Prověrka auta", "en": "Car check"},
    "checks.loading": {"cs": "Ověřuji…", "en": "Checking…"},
    "checks.no_vin": {
        "cs": "Inzerát neuvádí VIN. Požádejte o něj prodejce: bez něj nejde ověřit historii "
        "ani registr.",
        "en": "The ad gives no VIN. Ask the seller for it: without it neither the history nor "
        "the register can be checked.",
    },
    "checks.vin_make": {
        "cs": "VIN patří značce {makes}, inzerát uvádí jinou",
        "en": "The VIN belongs to {makes}; the ad says another make",
    },
    "checks.vin_make_ok": {"cs": "VIN odpovídá značce {makes}", "en": "The VIN fits {makes}"},
    "checks.vin_year": {
        "cs": "VIN ukazuje modelový rok {year}, nesedí s rokem v inzerátu",
        "en": "The VIN shows model year {year}, which does not fit the ad's year",
    },
    "checks.vin_year_ok": {
        "cs": "VIN: modelový rok {year}",
        "en": "VIN: model year {year}",
    },
    "checks.reg_seized": {
        "cs": "Registr: technický průkaz nebo SPZ jsou zadržené",
        "en": "Register: the papers or plates are seized",
    },
    "checks.reg_status": {
        "cs": "Registr: stav vozidla „{status}“",
        "en": "Register: the car's status is “{status}”",
    },
    "checks.reg_make": {
        "cs": "Registr: značka {make}, inzerát uvádí jinou",
        "en": "Register: make {make}; the ad says another",
    },
    "checks.reg_year": {
        "cs": "Registr: první registrace {first}, inzerát uvádí novější rok",
        "en": "Register: first registered {first}; the ad gives a later year",
    },
    "checks.reg_power": {
        "cs": "Registr: výkon {kw} kW, inzerát uvádí jiný",
        "en": "Register: {kw} kW; the ad says otherwise",
    },
    "checks.reg_imported": {
        "cs": "Registr: v ČR až od {imported}, inzerát píše koupeno v ČR",
        "en": "Register: in Czechia only since {imported}; the ad says bought in Czechia",
    },
    "checks.reg_owners": {
        "cs": "Registr: počet majitelů {owners}, inzerát píše 1. majitel",
        "en": "Register: {owners} owners; the ad says first owner",
    },
    "checks.first_registration": {
        "cs": "První registrace {date}",
        "en": "First registered {date}",
    },
    "checks.imported": {
        "cs": "dovezeno, v ČR od {date}",
        "en": "imported, in Czechia since {date}",
    },
    "checks.new_here": {"cs": "od nového v ČR", "en": "in Czechia from new"},
    "checks.owners": {
        "cs": "V registru {owners}× majitel, {operators}× provozovatel",
        "en": "On record: {owners} owners, {operators} operators",
    },
    "checks.on_record": {"cs": "V registru:", "en": "On record:"},
    "checks.stk_until": {"cs": "STK platí do {date}", "en": "Inspection valid until {date}"},
    "checks.status": {"cs": "Stav: {status}", "en": "Status: {status}"},
    "checks.not_registered": {
        "cs": "Registr vozidel toto VIN nezná (auto v ČR ještě nebylo registrováno?).",
        "en": "The vehicle register does not know this VIN (not registered in Czechia yet?).",
    },
    "checks.km_rollback": {
        "cs": "Toto VIN bylo dříve inzerováno s vyšším stavem km",
        "en": "This VIN was advertised before with a higher mileage",
    },
    "checks.km_seen": {"cs": "Stav km v inzerátech:", "en": "Mileage in the ads:"},
    "checks.offline": {
        "cs": "Registry teď nejsou dostupné, zkusím to později.",
        "en": "The registers cannot be reached now; I will try later.",
    },
    "checks.register_key": {
        "cs": "Údaje z registru vozidel uvidíte s bezplatným klíčem:",
        "en": "With a free key you will see the vehicle register's data:",
    },
    "checks.read_at": {"cs": "Registr ověřen {date}.", "en": "Register checked {date}."},
    "checks.again": {"cs": "Ověřit znovu", "en": "Check again"},
    "checks.by_hand": {"cs": "ověřte sami:", "en": "check yourself:"},
    "checks.link.km": {"cs": "stav km z STK", "en": "inspection mileage"},
    "checks.link.stolen": {"cs": "pátrání po vozidlech", "en": "stolen cars"},
    "checks.link.register": {"cs": "registr vozidel", "en": "vehicle register"},
    "checks.claims_note": {
        "cs": "Servisní knížku, výměny oleje ani nehody žádný bezplatný registr neeviduje: "
        "v inzerátu jde o tvrzení prodejce.",
        "en": "No free register has the service book, oil changes or accidents: in the ad "
        "they are the seller's claims.",
    },
    "dealer.cars": {"cs": "v nabídce aut: {n}", "en": "{n} cars on offer"},
    "dealer.match.pending": {
        "cs": "Firmu za prodejcem ještě hledám.",
        "en": "Still looking up the company.",
    },
    "dealer.match.uncertain": {
        "cs": "Není jisté, která firma to je. Vyberte ji:",
        "en": "It is not certain which company this is. Pick it:",
    },
    "dealer.match.none": {
        "cs": "Firmu tohoto jména obchodní rejstřík nezná.",
        "en": "The company register knows no company of this name.",
    },
    "dealer.match.user": {"cs": "vybráno vámi", "en": "picked by you"},
    "dealer.this_one": {"cs": "Tahle", "en": "This one"},
    "dealer.ended_short": {"cs": "zaniklá", "en": "dissolved"},
    "dealer.not_checked": {"cs": "Firma zatím nebyla ověřena.", "en": "Not checked yet."},
    "dealer.not_in_ares": {
        "cs": "IČO v obchodním rejstříku (ARES) není.",
        "en": "The IČO is not in the company register (ARES).",
    },
    "dealer.ended": {"cs": "Firma zanikla {date}", "en": "Dissolved on {date}"},
    "dealer.founded": {"cs": "Firma existuje od {date}", "en": "In business since {date}"},
    "dealer.insolvency": {"cs": "Insolvenční řízení", "en": "Insolvency proceedings"},
    "dealer.since": {"cs": "od", "en": "since"},
    "dealer.isir.pending": {"cs": "nevyřízené", "en": "pending"},
    "dealer.isir.moratorium": {"cs": "moratorium", "en": "moratorium"},
    "dealer.isir.bankrupt": {"cs": "úpadek", "en": "bankrupt"},
    "dealer.isir.konkurs": {"cs": "konkurs", "en": "bankruptcy"},
    "dealer.isir.debt_relief": {"cs": "oddlužení", "en": "debt relief"},
    "dealer.isir.reorganization": {"cs": "reorganizace", "en": "reorganisation"},
    "dealer.isir.ended": {"cs": "skončené", "en": "ended"},
    "dealer.isir.cancelled": {"cs": "zrušené", "en": "cancelled"},
    "dealer.isir.error": {"cs": "zapsáno omylem", "en": "entered in error"},
    "dealer.no_insolvency": {"cs": "Bez insolvence", "en": "No insolvency"},
    "dealer.vat_unreliable": {
        "cs": "Nespolehlivý plátce DPH od {date}",
        "en": "Unreliable VAT payer since {date}",
    },
    "dealer.vat_ok": {"cs": "Spolehlivý plátce DPH", "en": "Reliable VAT payer"},
    "dealer.seat": {"cs": "Sídlo:", "en": "Seat:"},
    "dealer.checked": {"cs": "Ověřeno {date}", "en": "Checked {date}"},
    "dealer.wrong": {"cs": "Jiná firma? Zadejte IČO", "en": "Another company? Enter its IČO"},
    "dealer.give_ico": {"cs": "Znáte IČO prodejce?", "en": "Do you know the dealer's IČO?"},
    "dealer.fix": {"cs": "Použít", "en": "Use"},
    "dealer.bad_ico": {
        "cs": "Firma s tímto IČO v rejstříku není.",
        "en": "No company with this IČO in the register.",
    },
    "dealer.google": {"cs": "Google Maps, {n} hodnocení", "en": "Google Maps, {n} ratings"},
    "dealer.reviews": {"cs": "Recenze ({n})", "en": "Reviews ({n})"},
    "dealer.google_key": {
        "cs": "Hodnocení z Google Map uvidíte s klíčem Google:",
        "en": "With a Google key you will see ratings from Google Maps:",
    },
    "dealer.no_google": {
        "cs": "Na Google Mapách bez hodnocení.",
        "en": "No ratings on Google Maps.",
    },
    "dealer.link.google": {"cs": "Google Maps", "en": "Google Maps"},
    "dealer.link.mapy": {"cs": "Mapy.com", "en": "Mapy.com"},
    "dealer.link.firmy": {"cs": "Firmy.cz", "en": "Firmy.cz"},
    "dealer.link.ares": {"cs": "ARES", "en": "ARES"},
    "dealer.link.justice": {"cs": "Obchodní rejstřík", "en": "Business register"},
    "dealer.link.isir": {"cs": "Insolvenční rejstřík", "en": "Insolvency register"},
    "filters.checks": {"cs": "Prověrky", "en": "Checks"},
    "filters.no_warnings": {"cs": "Bez varování", "en": "No warnings"},
    "filters.verified_dealer": {"cs": "Prověřený prodejce", "en": "Checked dealer"},
    "filters.with_vin": {"cs": "Uvedené VIN", "en": "VIN given"},
    "filters.cheaper": {"cs": "Jen zlevněná", "en": "Only cheaper"},
    "dealer.match.person": {
        "cs": "Prodejce je fyzická osoba (OSVČ): AdaCar o osobách údaje nevyhledává.",
        "en": "The dealer is a person in business: AdaCar does not look up persons.",
    },
    "dealer.match.error": {
        "cs": "Obchodní rejstřík toto jméno nepřijal; zkusím to za měsíc.",
        "en": "The company register did not accept this name; I will try in a month.",
    },
    "dealer.person": {
        "cs": "Toto IČO patří fyzické osobě: AdaCar o osobách údaje neukládá.",
        "en": "This IČO belongs to a person: AdaCar keeps no data on persons.",
    },
}


def translate(lang: str, key: str, **kwargs: object) -> str:
    entry = TEXTS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry["cs"]
    return text.format(**kwargs) if kwargs else text
