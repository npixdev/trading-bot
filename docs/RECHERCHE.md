# Recherche de stratégie — protocole pré-enregistré

> **Écrit et commité le 2026-10-22, AVANT d'avoir lancé le moindre test.**
> Les hypothèses, paramètres et critères ci-dessous ne seront pas modifiés après coup.
> Toute idée testée en dehors de cette liste sera signalée comme « exploratoire » (non probante).

## Pourquoi pré-enregistrer

À l'étape 2, une stratégie **au hasard** a affiché +842 % en backtest ; à l'étape 3, la « meilleure » des 9 combinaisons (89 % du témoin aléatoire) était cohérente avec la chance. Plus on teste d'idées, plus l'une d'elles finit par « marcher » par hasard. Fixer la liste et les critères à l'avance est la protection standard contre ce biais (*p-hacking*, *data snooping*).

## Données et conditions

- **Marché** : BTC/USDT, Binance, bougies **journalières**, du 2017-08-17 au 2026-10-21 (3 353 jours, 0 trou). Inclut le krach de 2018 (−84 %), 2021-2022 (−77 %), et plusieurs marchés haussiers.
- **Validation** : walk-forward glissant, entraînement 365 jours / test 91 jours. Hors échantillon d'environ août 2018 à octobre 2026 (~8 ans).
- **Coûts** : frais 0,10 % par ordre (taker OKX), slippage 0,05 %. Capital 10 000.
- **Choix des paramètres** : meilleur Sharpe sur la fenêtre d'entraînement uniquement.

## Hypothèses testées (liste fermée)

| # | Stratégie | Idée | Paramètres testés |
|---|---|---|---|
| H1 | **Filtre de tendance** | Investi à 100 % quand la clôture est au-dessus de sa moyenne mobile sur N jours, sinon cash. Éviter le gros des marchés baissiers. | N ∈ {50, 100, 150, 200} |
| H2 | **Ciblage de volatilité** | Poids = min(1, volatilité cible ÷ volatilité réalisée), arrondi au dixième. Investir moins quand le marché s'agite. | cible ∈ {40 %, 60 %, 80 %} annualisée ; fenêtre ∈ {20, 60} jours |
| H3 | **Tendance + volatilité** | H1 × H2 : le poids du ciblage de volatilité, seulement quand la tendance est haussière. | N ∈ {100, 200} ; cible ∈ {40 %, 60 %, 80 %} ; fenêtre 30 jours |
| H4 | **IA : régression logistique** | Modèle d'apprentissage automatique qui estime la probabilité que le lendemain soit haussier. Investi si probabilité > seuil. Réentraîné tous les 30 jours sur **tout le passé disponible à cette date uniquement**. | seuil ∈ {0,50 ; 0,52 ; 0,55} ; régularisation L2 λ ∈ {0,1 ; 1} |

**Variables du modèle H4** (toutes calculées avec les seules données connues à la clôture du jour) : rendements sur 1, 3, 7, 14 et 30 jours ; écart (log) à la moyenne mobile 20, 50 et 200 jours ; volatilité réalisée 7 et 30 jours ; rapport des volumes 7 jours / 30 jours. **Cible** : rendement du jour suivant > 0. Variables standardisées avec la moyenne et l'écart-type de l'échantillon d'entraînement seulement.

## Références (« juges »)

1. **Buy & hold** : tout investi dès le premier jour hors échantillon.
2. **DCA** (investissement programmé) : le capital investi en 52 versements hebdomadaires égaux sur la première année hors échantillon, puis conservé.
3. **Témoin à exposition constante** : même part moyenne investie que la stratégie, mais fixe (ex. 60 % BTC / 40 % cash), réajustée seulement si elle dérive de plus de 5 points. Il répond à : « la stratégie fait-elle mieux que *simplement investir moins* ? »
4. **Témoin aléatoire** (stratégies tout ou rien : H1, H4) : 100 stratégies au hasard de même exposition et même durée moyenne de position.

## Critères de succès (tous obligatoires)

Une hypothèse n'est déclarée **prometteuse** que si, hors échantillon :

1. son **Sharpe** dépasse celui du buy & hold **et** celui du témoin à exposition constante ;
2. son **max drawdown** est meilleur que celui du buy & hold d'au moins 10 points ;
3. pour H1 et H4, elle bat **au moins 95 %** des stratégies aléatoires. Le seuil passe de 90 % à 95 % pour tenir compte des 4 hypothèses testées.

Une hypothèse prometteuse **n'est pas une stratégie gagnante** : elle passerait ensuite plusieurs semaines en paper trading avant toute conclusion.

## Résultats

### Exécution unique du protocole (2026-10-22, commit `ddd1c9c`, commande `python -m bot research`)

Hors échantillon du **2018-08-17 au 2026-10-21** (8,2 ans), frais 0,10 % + slippage 0,05 %.

| | Rendement | CAGR | Max drawdown | Sharpe | Trades | Investi en moyenne | Verdict |
|---|---|---|---|---|---|---|---|
| Buy & hold | +1 229 % | +37,2 % | −76,6 % | 0,83 | 1 | 100 % | référence |
| DCA 52 semaines | +1 474 % | +40,1 % | −76,6 % | 0,87 | 52 achats | 100 % | référence |
| **H1 Filtre de tendance** | **+1 495 %** | **+40,3 %** | **−53,0 %** | **1,00** | 86 | 54 % | ✔ **prometteuse** |
| H2 Ciblage de volatilité | +314 % | +19,0 % | −76,0 % | 0,60 | 33 | 85 % | ✘ échec |
| **H3 Tendance + volatilité** | +748 % | +29,9 % | **−44,1 %** | **0,91** | 64 | 49 % | ✔ **prometteuse** |
| H4 IA (régression logistique) | **−45,7 %** | −7,2 % | −61,6 % | 0,06 | 425 | 38 % | ✘ échec |

Détail des critères :
- **H1** : Sharpe 1,00 > buy & hold 0,83 ✔ ; > exposition constante 54 % (0,83) ✔ ; drawdown −53 % vs −77 % ✔ ; meilleur que 97 % des stratégies aléatoires ✔.
- **H3** : Sharpe 0,91 > 0,83 ✔ ; > exposition constante 49 % (0,83) ✔ ; drawdown −44 % vs −77 % ✔ ; témoin aléatoire non applicable (poids fractionnaire).
- **H2** : Sharpe 0,60, inférieur au buy & hold comme à l'exposition constante 85 % ; drawdown quasi inchangé (−76 %).
- **H4** : Sharpe 0,06 ; 425 trades et 6 415 de frais ; Sharpe moyen de 0,65 sur les entraînements → 0,20 en test : **surapprentissage**. Prédire le sens du lendemain du BTC revient, à ces coûts, à jouer à pile ou face en payant à chaque lancer.

### Vérifications exploratoires (hors protocole, à titre indicatif)

Faites **après** avoir vu les résultats : elles ne comptent pas comme preuve, elles servent à comprendre.

- **Hasard, 200 tirages** : H1 meilleur que 96,5 % (rendement et Sharpe).
- **Paramètres fixes, sans optimisation** (même période) : SMA 50 → Sharpe 1,22 ; SMA 100 → 1,16 ; SMA 150 → 1,16 ; SMA 200 → 0,91 ; drawdowns de −39 % à −64 %. Toutes les fenêtres font au moins aussi bien que le buy & hold en Sharpe : le résultat ne repose pas sur un réglage précis. L'optimisation par fenêtre n'a pas aidé (elle a surtout choisi SMA 50, 17 fois sur 33).
- **Frais** (SMA 200 fixe) : Sharpe 0,91 à 0,08 % de frais, 0,86 à 0,40 % : robuste (≈ 3 à 4 trades par an).
- **Année par année** (H1 vs buy & hold) : 2018 −20 % vs −44 % ; 2019 +81 % vs +89 % ; 2020 +251 % vs +302 % ; 2021 +57 % vs +58 % ; 2022 −35 % vs −65 % ; 2023 +83 % vs +154 % ; 2024 +67 % vs +112 % ; 2025 −14 % vs −7 % ; 2026 (partiel) +10 % vs −5 %.

### Interprétation

1. **Le filtre de tendance gagne en évitant les krachs, pas en battant le marché quand il monte.** Toute l'avance vient de 2018 et 2022. Dans les années de forte hausse, il fait nettement moins bien (2023 : +83 % contre +154 %). Si le futur n'a pas de grand marché baissier, il fera probablement **moins bien** que le buy & hold.
2. **Il reste risqué** : −53 % de drawdown, et seulement 9 % de trades gagnants (beaucoup de petites pertes, quelques très gros gains) — psychologiquement difficile à suivre.
3. **Échantillon limité** : un seul actif, 8 ans, essentiellement 2 grands marchés baissiers. Le suivi de tendance est par ailleurs l'une des stratégies les mieux documentées sur les marchés, ce qui le rend plus crédible qu'une découverte isolée — mais aussi connu de tous.
4. **L'IA a échoué**, comme attendu : plus de variables et plus de trades, c'est plus de bruit et plus de frais.
5. **Résultat différent de l'étape 3** (croisement de moyennes, 2022-2026, « indiscernable du hasard ») : autre règle (prix vs moyenne), et surtout une période 2018-2026 qui contient deux krachs au lieu d'un.

---

# Recherche 2 — le filtre de tendance sur plusieurs cryptos (H5) — protocole pré-enregistré

> **Écrit et commité le 2026-10-22, AVANT d'avoir téléchargé les données des autres cryptos.**

## Motivation

H1 ne fait que 3 à 10 trades par an sur le seul BTC. Appliquer la même règle, indépendamment, à plusieurs cryptos donnerait plus d'activité **sans trader plus souvent chaque crypto**, et une diversification. Rien ne garantit que la règle marche ailleurs que sur le BTC : d'où ce test.

## Univers (fixé à l'avance, contre le biais du survivant)

Les **10 plus grosses cryptos par capitalisation vers août 2018** (début de la période de test), hors stablecoins, telles qu'on pouvait les connaître à l'époque — **pas** les gagnantes connues aujourd'hui :

**BTC, ETH, XRP, BCH, EOS, XLM, LTC, ADA, IOTA, TRX**, en paires `/USDT` sur Binance, bougies journalières.

Règles de données, fixées à l'avance :
- une crypto n'est tradée qu'à partir du moment où son historique Binance suffit à la stratégie (365 + 200 jours) ; avant, sa part reste en cash, **pour la stratégie comme pour les références** ;
- si l'historique s'arrête (retrait de la cote, changement de nom), la part est convertie en cash à la dernière clôture disponible ;
- si une paire n'existe pas du tout sur Binance, sa part reste en cash tout du long (signalé dans les résultats).

Limite connue : les cryptos complètement disparues de Binance ne peuvent pas être téléchargées ; un biais du survivant résiduel reste possible et sera signalé.

## Stratégie testée

**H5** : la stratégie H1 *telle que validée et mise en paper trading* (`trend_adaptive` : moyenne 50/100/150/200 jours re-choisie chaque trimestre sur le meilleur Sharpe de l'année écoulée), appliquée **indépendamment à chaque crypto**, avec **10 parts égales** de 1 000 (capital 10 000). Pas de nouveau paramètre, pas d'optimisation supplémentaire.

## Références

1. **Buy & hold équipondéré** : chaque part achète sa crypto à la même date que la stratégie peut commencer, puis conserve.
2. **Exposition constante** : chaque part investie en permanence à la même part moyenne que la stratégie, réajustée au-delà de 5 points.
3. **Témoin aléatoire** : 100 tirages ; dans chaque tirage, chaque part suit une stratégie au hasard de même exposition et même durée de position que la stratégie sur cette crypto.
4. **H1 sur le BTC seul**, pour comparaison.

## Critères de succès (identiques à la recherche 1)

Sur la période hors échantillon (à partir du 2018-08-17) :
1. Sharpe > buy & hold équipondéré **et** > exposition constante ;
2. max drawdown meilleur que le buy & hold équipondéré d'au moins 10 points ;
3. meilleur qu'au moins 95 % des tirages aléatoires.

Mesure d'activité (non critère) : nombre de trades par semaine sur l'ensemble du portefeuille.

## Résultats de la recherche 2

### Exécution unique (2026-10-22, commit `54d6b3e`, commande `python -m bot research-multi`)

Période 2018-08-17 → 2026-10-21. Les 10 paires existaient sur Binance ; EOS s'arrête le 2025-05-26 (changement de nom, part figée en cash) ; BCH ne commence qu'en 2019-11. Éligibilité (assez d'historique pour la stratégie) : BTC et ETH 2019-05, LTC 2019-08, XRP/EOS/XLM/ADA/IOTA/TRX 2020-01, BCH 2021-07.

| | Rendement | CAGR | Max drawdown | Sharpe | Trades | Frais |
|---|---|---|---|---|---|---|
| Buy & hold équipondéré | +533 % | +25,3 % | −81,2 % | 0,68 | 10 | 10 |
| Exposition constante (~43 %) | +343 % | +20,0 % | −48,1 % | 0,75 | 10 | 140 |
| **H5 : filtre de tendance × 10** | +416 % | +22,2 % | −57,7 % | 0,69 | 598 | 4 289 |
| *H1 sur le BTC seul (pour comparaison)* | +2 470 % | +48,7 % | −43,8 % | 1,15 | 56 | — |

Critères : Sharpe > buy & hold équipondéré ✔ (0,69 vs 0,68, de justesse) ; Sharpe > exposition constante ✘ (0,75) ; drawdown −10 pts ✔ (−58 % vs −81 %) ; hasard ≥ 95 % ✘ (79 %). **Verdict : ÉCHEC du protocole.** Activité : 1,4 trade par semaine.

Détail par crypto (informatif, période éligible de chacune) — Sharpe stratégie / buy & hold : BTC 1,20 / 0,91 ; ETH 0,89 / 0,86 ; ADA 0,74 / 0,70 ; IOTA 0,41 / 0,24 ; LTC 0,26 / 0,36 ; BCH 0,16 / 0,27 ; XLM 0,35 / 0,64 ; TRX 0,44 / 0,90 ; XRP 0,24 / 0,74 ; EOS −0,06 / 0,23. La stratégie ne fait mieux que le buy & hold que sur 4 cryptos sur 10.

### Interprétation

1. **Diversifier sur des altcoins n'a rien apporté** : plus de trades et de frais, un rapport gain/risque pas meilleur que d'investir simplement 43 % du capital en permanence. Les altcoins ont des tendances plus heurtées : la règle s'y fait souvent piéger (achat après une hausse, revente après la chute suivante).
2. **Ce résultat affaiblit la confiance dans H1** : si le filtre de tendance captait un phénomène robuste des marchés crypto, il devrait marcher sur la plupart des cryptos. Il ne marche clairement que sur le BTC (et marginalement sur ETH). Deux lectures possibles, qu'on ne peut pas trancher avec ces données : soit le BTC a des tendances plus « propres » (actif dominant, plus liquide), soit le bon résultat de H1 doit beaucoup à la chance (deux krachs bien placés).
3. **Conséquence pratique** : on garde H1 sur le BTC seul en paper trading, avec des attentes **plus prudentes** qu'avant cette recherche. On n'ajoute pas les altcoins.

---

**Statut de la recherche 1** : H1 et H3 sont *prometteuses*, pas *gagnantes*. Étape suivante prévue par le protocole : paper trading. Avec ≈ 3 à 10 trades par an en journalier, des semaines de paper trading vérifieront la mécanique, pas la performance — celle-ci ne se jugera que sur des années.
