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

*(Section complétée après l'unique exécution du protocole — voir plus bas.)*
