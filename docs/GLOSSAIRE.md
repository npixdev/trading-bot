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

**Base / Quote** [0]
Dans la paire `BTC/EUR`, BTC est la **base** (ce que tu achètes), EUR la **quote** (ce avec quoi tu paies).
*Ex. : BTC/EUR = 60 000 → 1 BTC coûte 60 000 €.*

**Bid** [0]
Prix auquel un acheteur est prêt à acheter. Le « meilleur bid » est le plus haut.
*Ex. : meilleur bid à 60 000 € → si tu vends au marché, tu obtiens au mieux 60 000 €.*

**Buy & hold** [0]
Acheter et garder, sans rien faire. C'est **la référence** que toute stratégie doit battre (en rendement ou en risque).
*Ex. : 10 000 € de BTC achetés début 2023 à ~16 500 $ valaient plusieurs fois plus fin 2024. Un bot qui a fait +40 % sur la même période a « gagné », mais a fait bien pire que ne rien faire.*

**Carnet d'ordres (*order book*)** [0]
Liste en temps réel des ordres d'achat (bids) et de vente (asks) en attente, classés par prix.

**CASP / MiCA** [0]
**MiCA** : règlement européen sur les crypto-actifs. **CASP** : prestataire de services sur crypto-actifs agréé sous MiCA. Depuis le 1er juillet 2026, une plateforme doit être CASP pour servir les clients français.
*Ex. : Kraken est agréé en Irlande et peut, grâce au « passeport » européen, servir les clients en France.*

**CEX / DEX** [0]
**CEX** : exchange centralisé, une entreprise garde tes fonds (Kraken, OKX…). **DEX** : exchange décentralisé sur blockchain (Uniswap).
*Ex. : FTX (CEX) a fait faillite en 2022 avec ~8 Md$ de fonds clients manquants.*

**Exchange** [0]
Plateforme où l'on échange des cryptos contre d'autres cryptos ou des euros.

**Frais maker / taker** [0]
**Taker** : ton ordre s'exécute immédiatement contre un ordre existant (il *prend* de la liquidité) → frais plus élevés. **Maker** : ton ordre attend dans le carnet (il *ajoute* de la liquidité) → frais plus bas.
*Ex. : Kraken ~0,25 % maker / 0,40 % taker. Sur 1 000 € : 2,50 € en maker, 4 € en taker.*

**Futures / perpétuels** [0]
Contrats qui suivent le prix d'une crypto sans la posséder, généralement avec levier. **Interdits dans ce projet** jusqu'à nouvel ordre.

**Levier** [0]
Multiplier son exposition avec de l'argent emprunté. Multiplie les gains **et** les pertes.
*Ex. : levier x10 avec 1 000 € → tu contrôles 10 000 € de BTC. Le BTC baisse de 10 % → −1 000 € → ta mise est entièrement perdue (liquidation).*

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

**Paire** [0]
Les deux actifs échangés, notés `BASE/QUOTE`. Voir *Base / Quote*.

**Profondeur (*depth*)** [0]
Quantité disponible dans le carnet à chaque niveau de prix.
*Ex. : 0,5 BTC au meilleur ask, 1,2 BTC au niveau suivant → acheter 1 BTC « mange » deux niveaux.*

**Slippage (glissement)** [0]
Écart entre le prix attendu et le prix réellement obtenu.
*Ex. : tu voulais acheter à 60 010 €, tu obtiens une moyenne de 60 016 € → slippage de 6 € (0,01 %). Lors d'un krach, il peut atteindre plusieurs %.*

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

**Testnet / démo** [0]
Version de l'exchange avec de l'argent fictif, pour tester un bot en conditions quasi réelles sans risque.

**USDT / USDC** [0]
Les deux principaux stablecoins en dollars. En Europe, USDT est restreint sur la plupart des plateformes agréées MiCA ; USDC y est disponible.
