# Glossaire

> Chaque terme rencontré dans le projet, expliqué simplement avec un exemple chiffré.
> Classé par ordre alphabétique. L'étape où le terme apparaît est indiquée entre crochets.

---

**Aller-retour (*round trip*)** [0]
Un achat suivi de la vente correspondante. C'est l'unité de coût d'un trade.
*Ex. : frais 0,10 % par ordre → un aller-retour coûte 0,20 % de la somme engagée (2 € sur 1 000 €).*

**API (clé API)** [0]
Interface qui permet à un programme de parler à l'exchange. Une clé API = un identifiant + un secret qui autorisent le bot à lire ton solde et/ou passer des ordres. **Jamais** dans le code, et **jamais** avec le droit de retrait.
*Ex. : si ta clé fuit sans droit de retrait, un voleur peut faire des trades absurdes, mais pas vider ton compte vers son portefeuille.*

**Ask** [0]
Prix auquel un vendeur est prêt à vendre. Le « meilleur ask » est le plus bas.
*Ex. : meilleur ask à 60 010 € → c'est le prix minimum que tu paieras en achetant au marché.*

**ATR (*Average True Range*)** [4]
Amplitude moyenne d'une bougie sur les N dernières (gaps compris). Mesure de la volatilité en unités de prix, utilisée pour placer les stops et dimensionner les positions.
*Ex. : BTC à 80 000 avec un ATR journalier de 2 400 → une journée « normale » fait bouger le prix d'environ 3 %.*

**Backtest** [2]
Simulation d'une stratégie sur des données passées, bougie par bougie, avec frais et slippage. Toujours plus optimiste que la réalité : c'est un filtre pour éliminer les mauvaises idées, pas une promesse.
*Ex. : une stratégie qui fait −57 % en backtest fera presque sûrement pire en réel. Une qui fait +800 % doit d'abord être soupçonnée.*

**Attente exponentielle (*backoff*)** [5]
Après une erreur réseau, attendre de plus en plus longtemps avant de réessayer, pour ne pas marteler un exchange en difficulté (et se faire bannir).
*Ex. : vérification toutes les 10 s ; après 3 échecs : attentes de 20 s, 40 s, 80 s, puis reconnexion.*

**Base / Quote** [0]
Dans la paire `BTC/EUR`, BTC est la **base** (ce que tu achètes), EUR la **quote** (ce avec quoi tu paies).
*Ex. : BTC/EUR = 60 000 → 1 BTC coûte 60 000 €.*

**Bid** [0]
Prix auquel un acheteur est prêt à acheter. Le « meilleur bid » est le plus haut.
*Ex. : meilleur bid à 60 000 € → si tu vends au marché, tu obtiens au mieux 60 000 €.*

**Bandes de Bollinger** [3]
Enveloppe autour d'une moyenne mobile : moyenne ± k écarts-types. Quand le prix sort de la bande basse, il est « anormalement bas » par rapport à son passé récent.
*Ex. : moyenne 20 bougies = 80 000, écart-type = 1 500, k = 2 → bande basse 77 000, bande haute 83 000. Un prix à 76 500 est sous la bande.*

**Bougie (*candle*)** [1]
Résumé des prix sur une période fixe (le *timeframe*) : ouverture, plus haut, plus bas, clôture, volume. Voir *OHLCV*.
*Ex. : bougie BTC/USDT 1h du 2021-01-01 01:00 UTC : O=28 995, H=29 470, L=28 960, C=29 410 → le BTC a pris +1,4 % en une heure.*

**Bougie non clôturée** [1]
La bougie en cours : sa clôture, son plus haut et son plus bas peuvent encore changer. L'utiliser dans une décision revient à regarder le futur (look-ahead). Notre fetcher l'écarte toujours.
*Ex. : à 21h45, la bougie de 21h00 indique « close = 80 960 », mais à 22h00 elle pourrait clôturer à 80 500.*

**Buy & hold** [0]
Acheter et garder, sans rien faire. C'est **la référence** que toute stratégie doit battre (en rendement ou en risque).
*Ex. : 10 000 € de BTC achetés début 2023 à ~16 500 $ valaient plusieurs fois plus fin 2024. Un bot qui a fait +40 % sur la même période a « gagné », mais a fait bien pire que ne rien faire.*

**CAGR (taux de croissance annuel composé)** [2]
Le rendement « moyen par an » qui, composé, donne le rendement total. Permet de comparer des périodes de durées différentes.
*Ex. : +179 % en 5,8 ans → CAGR = 2,79^(1/5,8) − 1 ≈ +19,4 %/an. Ce n'est pas +179/5,8 = 31 %.*

**Carnet d'ordres (*order book*)** [0]
Liste en temps réel des ordres d'achat (bids) et de vente (asks) en attente, classés par prix.

**CASP / MiCA** [0]
**MiCA** : règlement européen sur les crypto-actifs. **CASP** : prestataire de services sur crypto-actifs agréé sous MiCA. Depuis le 1er juillet 2026, une plateforme doit être CASP pour servir les clients français.
*Ex. : Kraken est agréé en Irlande et peut, grâce au « passeport » européen, servir les clients en France.*

**CEX / DEX** [0]
**CEX** : exchange centralisé, une entreprise garde tes fonds (Kraken, OKX…). **DEX** : exchange décentralisé sur blockchain (Uniswap).
*Ex. : FTX (CEX) a fait faillite en 2022 avec ~8 Md$ de fonds clients manquants.*

**Canal de Donchian / cassure (*breakout*)** [3]
Le plus haut et le plus bas des N dernières bougies. Une « cassure » = le prix dépasse ce plus haut : signal qu'une tendance démarre peut-être.
*Ex. : plus haut des 55 derniers jours = 85 000, clôture à 85 500 → cassure, on achète. On sort si le prix clôture sous le plus bas des 20 derniers jours.*

**Drawdown (chute depuis un sommet)** [1]
Perte, en %, entre le plus haut atteint et le point actuel. Le *max drawdown* est la pire de ces chutes : c'est la douleur maximale qu'il aurait fallu encaisser.
*Ex. : BTC à 68 634 $ en novembre 2021, puis à ~15 800 $ en novembre 2022 → drawdown de −77 %. 10 000 € investis au sommet n'en valaient plus que 2 300.*

**Equity (courbe d'equity)** [2]
Valeur totale du portefeuille (cash + crypto au prix du moment) à chaque instant. Toutes les métriques en découlent.
*Ex. : 2 000 € de cash + 0,1 BTC à 80 000 € → equity = 10 000 €.*

**Exchange** [0]
Plateforme où l'on échange des cryptos contre d'autres cryptos ou des euros.

**Exposition** [2]
Part du temps (ou du capital) investie en crypto. Moins on est exposé, moins on subit les chutes… et les hausses.
*Ex. : exposition 50 % → investi la moitié des heures. Même sans aucun talent, on capte en moyenne ~50 % de la tendance (en log).*

**Frais maker / taker** [0]
**Taker** : ton ordre s'exécute immédiatement contre un ordre existant (il *prend* de la liquidité) → frais plus élevés. **Maker** : ton ordre attend dans le carnet (il *ajoute* de la liquidité) → frais plus bas.
*Ex. : Kraken ~0,25 % maker / 0,40 % taker. Sur 1 000 € : 2,50 € en maker, 4 € en taker.*

**Gap de prix** [4]
Saut de prix entre la clôture d'une bougie et l'ouverture de la suivante, sans transaction entre les deux. Un stop traversé par un gap s'exécute plus bas que prévu.
*Ex. : stop à 90, la bougie ouvre à 70 → vente à 70, perte de 30 % au lieu des 10 % prévus.*

**Gap (trou de données)** [1]
Bougies absentes de l'historique, en général pendant une maintenance de l'exchange. On ne les invente pas, mais il faut les connaître : un indicateur « sur 20 bougies » couvrira alors plus de 20 heures.
*Ex. : Binance, 13 août 2021 : 4 bougies 1h manquantes entre 01:00 et 06:00 UTC.*

**Futures / perpétuels** [0]
Contrats qui suivent le prix d'une crypto sans la posséder, généralement avec levier. **Interdits dans ce projet** jusqu'à nouvel ordre.

**Kill switch (coupe-circuit)** [4]
Arrêt définitif du bot quand sa perte depuis son plus haut dépasse un seuil. Protège contre un bug ou une stratégie cassée ; ne se réarme qu'à la main, après analyse.
*Ex. : seuil −30 %, capital passé de 10 000 à 6 900 → tout est vendu, plus aucun ordre.*

**Levier** [0]
Multiplier son exposition avec de l'argent emprunté. Multiplie les gains **et** les pertes.
*Ex. : levier x10 avec 1 000 € → tu contrôles 10 000 € de BTC. Le BTC baisse de 10 % → −1 000 € → ta mise est entièrement perdue (liquidation).*

**Écart-type** [3]
Mesure de dispersion : de combien les valeurs s'écartent typiquement de leur moyenne. En trading, c'est la mesure standard de la volatilité.
*Ex. : prix 98, 100, 102 → moyenne 100, écart-type ≈ 1,6. Prix 90, 100, 110 → écart-type ≈ 8,2 : beaucoup plus volatil.*

**Grille de paramètres** [3]
L'ensemble des combinaisons testées lors de l'optimisation. Plus elle est grande, plus on risque de trouver une combinaison gagnante par pur hasard.
*Ex. : fast ∈ {10, 20, 50} × slow ∈ {50, 100, 200} = 9 combinaisons (8 valides, car fast < slow).*

**Hors échantillon (*out-of-sample*, OOS)** [2]
Données qui n'ont **pas** servi à choisir la stratégie ou ses paramètres. Seule la performance hors échantillon a une valeur prédictive.
*Ex. : paramètres choisis sur 2021-2024 (Sharpe 2,0), évalués sur 2025 (Sharpe 0,3) → c'est 0,3 qu'il faut croire.*

**Moyenne mobile (SMA) / croisement** [3]
Moyenne des N derniers prix de clôture, recalculée à chaque bougie : une version lissée du prix. Le « croisement » compare une moyenne courte (réactive) à une longue (lente).
*Ex. : SMA 50 jours = 82 000, SMA 200 jours = 75 000 → la courte est au-dessus : tendance haussière, on est investi.*

**Limite de perte journalière** [4]
Si le portefeuille perd plus de X % depuis minuit UTC, on vend tout et on ne trade plus jusqu'au lendemain.
*Ex. : 13/06/2022, BTC −15,4 % sur la journée ; avec une limite à 5 %, la perte du jour est restée à −5,3 %.*

**Liquidation** [0]
Fermeture forcée d'une position à effet de levier quand ta mise ne couvre plus les pertes. Tu perds tout ce que tu avais mis.

**Liquidité** [0]
Facilité à acheter ou vendre une quantité sans faire bouger le prix. Un marché liquide a un spread faible et beaucoup de volume dans le carnet.
*Ex. : sur BTC/USDT, acheter 10 000 € ne bouge quasiment pas le prix ; sur une petite crypto obscure, ça peut le faire monter de 2 %.*

**Look-ahead bias (biais d'anticipation)** [0]
Erreur de backtest qui consiste à utiliser une information qu'on n'aurait pas eue au moment de décider. Rend les résultats faussement excellents.
*Ex. : décider « j'achète car la bougie de 14h a clôturé en hausse » et simuler l'achat au prix d'ouverture de 14h. En réalité, à 14h00 tu ne connais pas encore la clôture de 15h00. Correct : acheter à l'ouverture de la bougie suivante (15h).*

**Marge** [0]
Emprunter auprès de l'exchange pour acheter plus que ce qu'on a. Forme de levier. Interdit ici.

**Matching engine (moteur d'appariement)** [0]
Le programme de l'exchange qui fait se rencontrer les ordres d'achat et de vente compatibles.

**OHLCV** [1]
**O**pen (premier prix de la période), **H**igh (plus haut), **L**ow (plus bas), **C**lose (dernier prix), **V**olume (quantité échangée, en unité de base). Format standard des données historiques.
*Ex. : O=80 880, H=80 998, L=80 684, C=80 963, V=669 → sur cette heure le BTC a oscillé dans 314 $ de large, fini en hausse de 0,1 %, et 669 BTC (~54 M$) ont été échangés.*

**Ordre limit** [0]
Ordre à un prix maximum (achat) ou minimum (vente). Prix garanti, exécution non garantie.
*Ex. : BTC à 60 000 €, tu places un achat limit à 59 500 €. Si le prix ne descend jamais à 59 500 €, rien ne se passe.*

**Ordre market** [0]
Ordre exécuté immédiatement au meilleur prix disponible. Exécution garantie, prix non garanti.
*Ex. : tu achètes 0,01 BTC au marché, le meilleur ask est 60 010 € → tu paies ~600,10 € + frais taker.*

**Ordre partiel (*partial fill*)** [0]
Ordre seulement en partie exécuté, faute de contrepartie suffisante à ton prix.
*Ex. : achat limit de 1 BTC à 59 500 €, seuls 0,4 BTC sont vendus à ce prix → tu détiens 0,4 BTC, 0,6 BTC restent en attente.*

**Ordre post-only** [0]
Ordre limit qui est annulé s'il devait s'exécuter immédiatement. Garantit de payer les frais maker.

**Ordre stop-loss** [0]
Ordre dormant qui se déclenche quand le prix atteint un seuil, pour couper une perte.
*Ex. : achat à 60 000 €, stop à 57 000 € → la perte est limitée à ~5 % (un peu plus en cas de slippage).*

**Ordre stop-limit** [0]
Stop qui, une fois déclenché, place un ordre limit au lieu d'un market. Risque : ne pas être exécuté si le prix chute trop vite.
*Ex. : stop 57 000 €, limit 56 900 €. Le BTC passe de 57 100 € à 55 000 € en une minute → ton ordre à 56 900 € n'est jamais rempli, tu es toujours en position et ta perte continue.*

**Paper trading** [5]
Faire tourner le bot en temps réel sur les vrais prix, avec un portefeuille fictif : aucun ordre n'est envoyé. Teste la mécanique (données, signaux, garde-fous, pannes) sans risquer d'argent.
*Ex. : le bot « achète » 0,02 BTC au meilleur ask du moment (81 837,40 $) + slippage + frais ; seul le journal SQLite change.*

**Pagination** [1]
L'API ne renvoie qu'un nombre limité de bougies par requête (~1 000 sur Binance, 100 à 300 sur OKX). Pour tout l'historique, on enchaîne les requêtes en avançant le point de départ.
*Ex. : 2021-01-01 → 2026-10-18 en 1h ≈ 50 800 bougies ≈ 51 requêtes sur Binance.*

**Paire** [0]
Les deux actifs échangés, notés `BASE/QUOTE`. Voir *Base / Quote*.

**Position cible** [2]
Ce que la stratégie veut détenir, en fraction du capital : 0 = tout en cash, 1 = tout investi. Le moteur en déduit les ordres.
*Ex. : capital 10 000 €, cible 0,5 → le moteur achète pour ~5 000 € de BTC à l'ouverture de la bougie suivante.*

**Profondeur (*depth*)** [0]
Quantité disponible dans le carnet à chaque niveau de prix.
*Ex. : 0,5 BTC au meilleur ask, 1,2 BTC au niveau suivant → acheter 1 BTC « mange » deux niveaux.*

**Rate limit (limite de débit)** [1]
Nombre maximum de requêtes autorisées par l'exchange sur une période. Au-delà : erreur, puis bannissement temporaire de ton IP ou de ta clé. ccxt espace automatiquement les requêtes (`enableRateLimit`).
*Ex. : si l'exchange autorise ~20 requêtes/s et que ton bot en envoie 100 d'un coup, il peut être bloqué plusieurs minutes, au pire moment (pendant un krach).*

**Reprise d'état** [5]
Après un arrêt (crash, redémarrage), le bot recharge exactement son état : cash, BTC détenu, stop en cours, kill switch, dernière bougie traitée. Il ne rejoue pas les décisions manquées : on ne peut pas trader dans le passé.
*Ex. : arrêt de 5 h en 1h → au redémarrage, une seule décision (celle du moment) et une note « 5 bougie(s) non traitée(s) ».*

**Retour à la moyenne (*mean reversion*)** [3]
Pari qu'un prix qui s'est beaucoup éloigné de sa moyenne va y revenir. Gagne souvent un peu, perd parfois beaucoup (quand la chute continue).
*Ex. : win rate de 72 % mais rendement total −32 % en 4h : beaucoup de petits gains, quelques grosses pertes.*

**Risque par trade** [4]
Part du capital qu'on accepte de perdre si le stop est touché. Sert à calculer la taille de position : taille = risque ÷ distance du stop.
*Ex. : capital 10 000, risque 1 % = 100 €, stop 6 % sous l'entrée → on achète 100 / 0,06 ≈ 1 667 € de BTC.*

**RSI (*Relative Strength Index*)** [3]
Indicateur entre 0 et 100 qui compare les hausses récentes aux baisses récentes. < 30 = « survendu » (beaucoup de baisses), > 70 = « suracheté ».
*Ex. : sur 14 bougies, gains moyens 100 $, pertes moyennes 300 $ → RS = 1/3 → RSI = 100 − 100/(1 + 1/3) = 25 : survendu.*

**Sharpe (ratio de)** [2]
Rendement moyen ÷ volatilité, annualisé (taux sans risque pris à 0). Mesure le gain par unité de « secousse ». Repères : < 0 perd de l'argent ; 0,5 ≈ buy & hold BTC ; > 1 bon ; > 2 durablement : suspect (bug, look-ahead ou surapprentissage).
*Ex. : deux stratégies à +20 %/an ; l'une avec 20 % de volatilité (Sharpe 1), l'autre 80 % (Sharpe 0,25). La première est bien meilleure.*

**Slippage (glissement)** [0]
Écart entre le prix attendu et le prix réellement obtenu.
*Ex. : tu voulais acheter à 60 010 €, tu obtiens une moyenne de 60 016 € → slippage de 6 € (0,01 %). Lors d'un krach, il peut atteindre plusieurs %.*

**Sortino (ratio de)** [2]
Comme le Sharpe, mais ne compte comme « risque » que les baisses. Une stratégie avec de grosses hausses et de petites baisses a un Sortino nettement supérieur à son Sharpe.
*Ex. : BTC 2021-2026 : Sharpe 0,60, Sortino 0,84.*

**Spot** [0]
Achat réel de la crypto, payée intégralement. On ne peut pas perdre plus que sa mise. **Seul mode autorisé dans ce projet.**
*Ex. : 1 000 € de BTC, le BTC chute de 50 % → il te reste 500 €, jamais moins que 0.*

**Spread** [0]
Écart entre meilleur ask et meilleur bid. C'est un coût caché : acheter puis revendre immédiatement te le fait perdre.
*Ex. : ask 60 010 €, bid 60 000 € → spread 10 €, soit 0,017 %.*

**Stablecoin** [0]
Crypto conçue pour valoir 1 dollar (ou 1 euro). USDT, USDC. Sert de « cash » sur les exchanges.
*Ex. : vendre ton BTC contre de l'USDC te met « à l'abri » des variations du BTC, sans repasser par ta banque.*

**Surapprentissage (*overfitting*)** [0]
Ajuster une stratégie si précisément au passé qu'elle capture du bruit (du hasard) au lieu d'un vrai phénomène. Elle s'effondre sur des données nouvelles.
*Ex. : tu testes 500 combinaisons de paramètres, la meilleure affiche +300 % sur 2021-2023. Sur 2024, elle fait −15 % pendant que le buy & hold fait +100 %.*

**Train / test** [2]
Découpage chronologique de l'historique : on choisit les paramètres sur la partie *train* (entraînement), on juge sur la partie *test* jamais vue. Jamais de mélange aléatoire : le futur ne doit pas servir à prédire le passé.
*Ex. : 70/30 sur 2021-2026 → train 2021-01 à 2025-01, test 2025-01 à 2026-10.*

**Stop-loss dans le bot** [4]
Prix de sortie fixé à l'entrée (ici en multiple d'ATR ou en %), vérifié pendant chaque bougie. Après un stop, le bot ne rachète pas tant que la stratégie n'est pas d'abord repassée à 0.
*Ex. : entrée à 80 000, ATR 2 000, stop à 3 ATR → sortie automatique à 74 000 (−7,5 %).*

**Suivi de tendance (*trend following*)** [3]
Famille de stratégies qui achètent ce qui monte et sortent de ce qui baisse (moyennes mobiles, Donchian). Peu de trades gagnants, mais quelques très gros gains. Leur intérêt principal : réduire les grosses chutes.
*Ex. : win rate de 21 %, mais max drawdown −36 % au lieu de −67 % pour le buy & hold.*

**Témoin aléatoire (percentile)** [3]
On simule 100 stratégies au hasard avec la même exposition et la même durée moyenne de position, et on regarde quelle part d'entre elles la vraie stratégie bat. Sous ~90 %, rien ne prouve qu'elle fait mieux qu'un tirage au sort.
*Ex. : « bat 50 % » = exactement aussi bonne qu'une stratégie au hasard qui serait investie aussi souvent.*

**Tests multiples** [3]
Plus on teste de stratégies, plus l'une d'elles finit par paraître « significative » par chance. Avec 9 essais indépendants au seuil de 90 %, on a ~61 % de chances qu'au moins un passe par hasard.
*Ex. : 3 stratégies × 3 timeframes = 9 essais ; le meilleur à 89 % n'a rien d'étonnant.*

**Taille de position (*sizing*)** [4]
Combien acheter à chaque entrée. En % fixe du capital, ou calculée à partir du risque par trade et de la volatilité (ATR) : plus le marché est agité, plus la position est petite.
*Ex. : SMA 1d avec 1 % de risque → positions entre 6 % et 16 % du capital au lieu de 100 %.*

**Testnet / démo** [0]
Version de l'exchange avec de l'argent fictif, pour tester un bot en conditions quasi réelles sans risque.

**Timeframe (unité de temps)** [1]
Durée d'une bougie : 1m, 5m, 1h, 4h, 1d… Plus il est court, plus il y a de bruit, de trades potentiels et donc de frais.
*Ex. : un an = 525 600 bougies 1m, 8 760 bougies 1h, 365 bougies 1d.*

**USDT / USDC** [0]
Les deux principaux stablecoins en dollars. En Europe, USDT est restreint sur la plupart des plateformes agréées MiCA ; USDC y est disponible.

**Walk-forward** [2]
Répéter train/test en glissant dans le temps : optimiser sur 1 an, tester sur les 3 mois suivants, avancer de 3 mois, recommencer. Les périodes de test mises bout à bout forment une performance hors échantillon sur plusieurs régimes de marché.
*Ex. : 2021-2026 avec train 1 an / test 3 mois → 20 fenêtres de test, de janv. 2022 à oct. 2026.*

**Whipsaw (aller-retour perdant)** [4]
Vendre après une baisse, puis racheter plus haut quand le prix rebondit. Chaque aller-retour coûte la baisse encaissée plus les frais.
*Ex. : limite journalière sur un buy & hold BTC 1h : 141 ventes forcées puis rachats le lendemain → +24 % au lieu de +179 %.*

**Win rate (taux de réussite)** [2]
Part des trades clôturés gagnants. Seul, il ne veut rien dire : on peut gagner 90 % du temps et perdre de l'argent si les 10 % de pertes sont énormes.
*Ex. : 9 trades à +1 % et 1 trade à −15 % → win rate 90 %, résultat ≈ −6,7 %.*
