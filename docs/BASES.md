# Les bases du trading crypto

> Document de l'étape 0. Le but : comprendre le terrain de jeu avant d'écrire la moindre ligne de bot.
> Les termes en **gras** sont détaillés dans [GLOSSAIRE.md](GLOSSAIRE.md).

---

## 1. Qu'est-ce qu'un exchange crypto ?

Un **exchange** (plateforme d'échange) est une place de marché où des acheteurs et des vendeurs se rencontrent. Il en existe deux grandes familles :

- **CEX (exchange centralisé)** : Binance, Kraken, Coinbase, OKX… Une entreprise gère la plateforme, **garde tes fonds** (tu lui fais confiance), fait le rapprochement entre acheteurs et vendeurs, et expose une API. C'est ce qu'on utilisera, via `ccxt`.
- **DEX (exchange décentralisé)** : Uniswap… Des contrats sur une blockchain, pas d'intermédiaire. Plus complexe, hors périmètre du projet.

Ce que fait concrètement un CEX :
1. Tu déposes des euros (virement SEPA) ou des cryptos.
2. Tu passes des ordres d'achat/vente ; le **moteur d'appariement** (*matching engine*) les fait se rencontrer avec ceux des autres.
3. L'exchange prélève des **frais** sur chaque transaction.
4. Tu peux retirer tes fonds vers ta banque ou un portefeuille personnel.

**Risque propre aux CEX** : tant que tes fonds sont sur l'exchange, ils ne sont pas vraiment à toi. Une faillite (FTX en 2022 : ~8 milliards de dollars de fonds clients manquants) ou un piratage peut tout faire disparaître. Règle : ne laisse sur l'exchange que ce dont le bot a besoin.

---

## 2. Une paire : BTC/USDT

On n'achète jamais « du bitcoin » dans l'absolu : on **échange** une monnaie contre une autre. Une paire s'écrit `BASE/QUOTE` :

- **base** = ce que tu achètes ou vends (ici BTC)
- **quote** (cotation) = ce avec quoi tu paies (ici USDT)

« BTC/USDT = 60 000 » signifie : **1 BTC coûte 60 000 USDT**.

- Acheter 0,01 BTC sur BTC/USDT → tu dépenses 600 USDT, tu reçois 0,01 BTC.
- Vendre 0,01 BTC → tu rends 0,01 BTC, tu reçois 600 USDT (moins les frais).

**Stablecoins** : USDT (Tether) et USDC sont des jetons censés valoir 1 dollar. Ils servent de « cash » sur les exchanges.
⚠️ **En Europe**, la réglementation MiCA a conduit la plupart des plateformes agréées à restreindre USDT pour les clients européens. En pratique, depuis la France tu utiliseras plutôt **BTC/EUR** ou **BTC/USDC**. Pour *télécharger de l'historique* (étape 1), BTC/USDT reste très utile car c'est la paire la plus liquide du monde avec le plus long historique.

---

## 3. Le carnet d'ordres (*order book*)

C'est la liste, en temps réel, de toutes les intentions d'achat et de vente en attente :

```
        VENTES (asks) — des gens veulent vendre
        60 030 €   0,80 BTC
        60 015 €   1,20 BTC
        60 010 €   0,50 BTC   ← meilleur ask (le vendeur le moins cher)
  ─────────────── spread = 60 010 − 60 000 = 10 € ───────────────
        60 000 €   0,30 BTC   ← meilleur bid (l'acheteur le plus généreux)
        59 990 €   2,00 BTC
        59 970 €   1,50 BTC
        ACHATS (bids) — des gens veulent acheter
```

- **Bid** : prix auquel quelqu'un est prêt à acheter. **Ask** : prix auquel quelqu'un est prêt à vendre.
- **Spread** : écart entre meilleur ask et meilleur bid. Ici 10 € sur 60 000 €, soit ~0,017 %. Si tu achètes puis revends immédiatement, tu perds ce spread.
- **Profondeur** (*depth*) : la quantité disponible à chaque niveau. Plus il y a de volume proche du prix, plus le marché est **liquide**.

Le « prix » affiché d'une crypto est en général le prix de la **dernière transaction**, quelque part entre bid et ask.

---

## 4. Les types d'ordres

| Ordre | Ce que tu dis | Avantage | Inconvénient |
|---|---|---|---|
| **Market** | « Achète maintenant, au meilleur prix disponible » | Exécution garantie et immédiate | Prix non garanti ; tu paies le spread et les frais *taker* |
| **Limit** | « Achète seulement à 59 950 € ou moins » | Prix garanti, souvent frais *maker* plus bas | Peut ne jamais s'exécuter si le prix n'y va pas |
| **Stop-loss (stop-market)** | « Si le prix descend à 57 000 €, vends au marché » | Limite automatiquement une perte | En cas de chute brutale, exécuté plus bas que prévu (slippage) |
| **Stop-limit** | « Si le prix touche 57 000 €, place un limit à 56 900 € » | Contrôle du prix | Peut ne pas s'exécuter du tout si le marché chute trop vite → perte non limitée |
| **Post-only** | Un limit qui est annulé s'il devait s'exécuter immédiatement | Garantit les frais *maker* | Peut être rejeté |

**Ordre partiel** : un ordre limit de 1 BTC peut n'être rempli qu'à 0,4 BTC si seulement 0,4 BTC était disponible à ton prix. Le bot devra gérer ça (étape 6).

**Slippage** : différence entre le prix que tu attendais et le prix obtenu.
Exemple : tu veux acheter 2 BTC au marché. Dans le carnet ci-dessus, tu prends 0,50 à 60 010, 1,20 à 60 015, puis 0,30 à 60 030 → prix moyen ≈ 60 016 €, alors que tu « voyais » 60 010 €. Avec un petit capital, le slippage dépend surtout de la volatilité et de la latence ; c'est pourquoi on le modélise toujours dans nos backtests.

---

## 5. Les frais

### Maker vs taker
- **Taker** : ton ordre *prend* de la liquidité existante (ordre market, ou limit exécuté immédiatement). Frais plus élevés.
- **Maker** : ton ordre *ajoute* de la liquidité au carnet (limit qui attend). Frais plus bas, parfois nuls.

### Exemple chiffré
Frais taker de 0,10 %. Tu achètes pour 1 000 € de BTC puis tu revends au même prix :
- achat : 1 000 € × 0,10 % = 1,00 €
- vente : 1 000 € × 0,10 % = 1,00 €
- **un aller-retour coûte 0,20 %**, sans compter spread et slippage.

Ça paraît peu. Mais un bot qui fait **200 allers-retours par an** paie 200 × 0,20 % ≈ **40 % de son capital en frais par an** (un peu moins avec la capitalisation, mais l'ordre de grandeur est là). Il doit gagner plus que ça juste pour être à zéro. Avec des frais à 0,40 % (taker sur certaines plateformes), c'est 0,80 % par aller-retour → 200 trades = le capital y passe.

**Leçon n°1 : moins un bot trade, moins il a besoin d'être bon.**

### Autres coûts
- **Spread et slippage** (voir ci-dessus).
- **Frais de dépôt/retrait** : virement SEPA souvent gratuit ou ~1 € ; retrait crypto selon le réseau.
- **Impôts (France)** : les plus-values réalisées lors d'une **conversion crypto → euros** (ou achat d'un bien) sont imposables au prélèvement forfaitaire unique (« flat tax », environ 30 % ; vérifie le taux en vigueur). Les échanges crypto → crypto ne sont pas imposables en eux-mêmes. Les comptes ouverts sur des exchanges étrangers doivent être déclarés (formulaire 3916-bis). Un bot qui fait beaucoup de trades vers l'euro complique beaucoup la déclaration. Ce n'est pas un conseil fiscal : renseigne-toi.

---

## 6. Spot vs futures, et pourquoi le levier est interdit ici

- **Spot** : tu achètes réellement la crypto. Si tu achètes 1 000 € de BTC et que le BTC baisse de 50 %, il te reste 500 €. Tu ne peux **pas perdre plus que ta mise**.
- **Futures / perpétuels** : contrats qui suivent le prix, souvent avec **levier**. Avec un levier x10, 1 000 € contrôlent 10 000 € de BTC. Une baisse de **10 %** suffit à **perdre la totalité** de ta mise (**liquidation**). Le BTC a déjà fait −10 % en une journée de nombreuses fois.
- **Marge** : emprunter pour acheter plus. Même problème.

C'est pour ça que ce projet est **spot uniquement**, au moins jusqu'à la fin de l'étape 5.

---

## 7. Pourquoi la plupart des bots perdent de l'argent

Ce n'est pas du pessimisme, c'est la base pour rester lucide :

1. **Les frais mangent tout.** (voir §5) Une stratégie qui gagne 0,15 % par trade en moyenne *avant* frais perd de l'argent avec 0,20 % de frais par aller-retour.
2. **Le surapprentissage** (*overfitting*). En testant 1 000 combinaisons de paramètres sur le passé, on finit toujours par en trouver une qui aurait gagné… par hasard. Elle ne marchera pas dans le futur. C'est l'erreur n°1 des débutants, et la raison pour laquelle on séparera données d'entraînement et données de test (étape 2).
3. **Les biais de backtest.** Le plus grave : le **look-ahead bias**, utiliser sans le vouloir une information du futur (ex. : décider avec le prix de clôture d'une bougie et acheter au prix d'ouverture de cette même bougie). Le backtest devient magnifique et totalement faux. On a un test dédié contre ça.
4. **Le marché change de régime.** Une stratégie de tendance excelle en 2020-2021 (forte hausse) et saigne en 2022 (baisse) ou en marché plat. Le passé ne se répète pas à l'identique.
5. **Le buy & hold est dur à battre.** Sur le BTC, simplement acheter et garder a historiquement rapporté énormément (avec des chutes de −70 % à −80 %). Beaucoup de bots « gagnent de l'argent » mais moins que le buy & hold, donc ne servent à rien sauf s'ils réduisent fortement le risque.
6. **La concurrence.** En face, il y a des firmes professionnelles avec des serveurs collés à l'exchange, des données meilleures et des frais quasi nuls. Les petites inefficacités faciles sont exploitées depuis longtemps.
7. **Les risques opérationnels.** Bug qui passe 100 ordres au lieu d'un, perte de connexion pendant un krach, clé API volée, exchange qui ferme… Un bon bot passe plus de lignes de code à **se protéger** qu'à trader.
8. **L'humain.** On coupe le bot après trois pertes, on augmente la mise après trois gains, on « corrige » la stratégie à chaud. Le bot n'a d'intérêt que s'il est discipliné.

**Ce qu'on peut raisonnablement espérer** : apprendre énormément, et peut-être une stratégie qui fait *à peu près* comme le buy & hold avec des chutes moins profondes. Pas devenir riche. Et **n'engage jamais d'argent que tu ne peux pas te permettre de perdre entièrement**.

---

## 8. Choisir un exchange pour commencer

> ⚠️ Situation vérifiée en **octobre 2026** sur des sources secondaires (presse spécialisée). La réglementation bouge vite : **vérifie toujours le registre officiel** avant d'ouvrir un compte — [registre ESMA des CASP (MiCA)](https://www.esma.europa.eu/) et la [liste blanche de l'AMF](https://www.amf-france.org/).

### Le contexte réglementaire
Depuis le **1er juillet 2026**, fin de la période de transition du règlement européen **MiCA** : seules les plateformes **agréées CASP** (en France ou dans un autre pays de l'UE, avec « passeport » européen) peuvent servir les clients français. **Binance n'a pas obtenu d'agrément** : la presse rapporte la suspension de ses services en France (plus de nouveaux clients ni de dépôts, retraits seulement). L'AMF rappelle qu'opérer sans agrément est un délit.

**Conséquence** : Binance reste utile comme **source de données historiques publiques** (pas besoin de compte), mais n'est **pas un choix raisonnable pour trader depuis la France** aujourd'hui.

### Les critères
1. **Agréé MiCA et accessible en France** — non négociable.
2. **Frais spot** bas, surtout *taker* (un bot passe souvent des ordres market).
3. **Testnet ou mode démo** accessible via ccxt — indispensable pour l'étape 6.
4. **Support ccxt** solide (données + ordres).
5. **Liquidité** sur les paires en EUR/USDC, et historique de données disponible.
6. **Réputation et sécurité** (ancienneté, incidents passés).

### Comparatif (à revérifier)

| Exchange | MiCA (pays d'agrément) | Frais spot de base (maker / taker) | Testnet / démo via ccxt | Commentaire |
|---|---|---|---|---|
| **OKX** | ✅ Malte (janv. 2025) | ~0,08 % / 0,10 % | ✅ « Demo trading » (`set_sandbox_mode`) | Frais bas, bon mode démo. Historique réputationnel plus mitigé. |
| **Kraken** | ✅ Irlande | ~0,25 % / 0,40 % | ❌ pas de testnet spot | Très bonne réputation, paires EUR liquides. Frais nettement plus élevés, pas de bac à sable spot → l'étape 6 se ferait directement en réel avec de petits montants. |
| **Coinbase (Advanced)** | ✅ (Luxembourg ou Irlande selon sources) | ~0,40 % / 0,60 % (petits volumes) | ❌ pas de sandbox spot utilisable via ccxt | Simple et réputé, mais les frais les plus élevés : très pénalisant pour un bot. |
| **Bybit (Bybit EU)** | ✅ Autriche (à confirmer pour la France) | ~0,10 % / 0,10 % | ⚠️ testnet de la plateforme globale ; compatibilité avec l'entité EU à vérifier | Statut français moins clair dans les sources. |
| **Binance** | ❌ pas d'agrément | 0,10 % / 0,10 % | ✅ testnet spot | **Exclu pour trader** depuis la France. OK pour télécharger l'historique. |

Les frais baissent avec le volume mensuel ; avec un petit capital tu resteras au palier de base.

### Ce que ça change selon le choix
- **OKX** : le meilleur compromis « frais + démo » pour un bot. Le chemin backtest → paper → démo → live se fait sur la même plateforme.
- **Kraken** : le plus « tranquille » côté confiance, mais 0,80 % par aller-retour en ordres market impose une stratégie qui trade **très peu** (quelques fois par mois au maximum), ou l'usage d'ordres limit (frais maker).
- **Données historiques** : on peut très bien faire les backtests avec l'historique BTC/USDT de Binance (le plus long et le plus liquide) puis trader BTC/EUR ailleurs. Les prix sont quasi identiques ; seuls les frais et le spread diffèrent, et on les paramètre.

Le code reste de toute façon **agnostique** : changer d'exchange = changer une ligne de config.

### Sources consultées
- [Absence d'agrément MiCA pour Binance d'ici 2026 : conséquences en France](https://www.cyriljarnias.fr/consequences-absence-agrement-mica-binance-2026-france/)
- [MiCA Binance France : ma plateforme reste-t-elle légale en juillet 2026 ?](https://www.cryptoreflex.fr/blog/mica-binance-france-2026)
- [Binance ferme ses services UE faute de licence MiCA](https://www.france-epargne.fr/news/binance-ferme-ses-services-aux-clients-de-lue-faute-de-licence-mica-avant-le-1er-juillet)
- [MiCA : avec les retardataires estivaux, la France compte 35 CASP agréés](https://www.mind.eu.com/fintech/article/mica-avec-les-retardataires-estivaux-la-france-compte-desormais-35-casp-agrees/)
- [MiCA juillet 2026 : état des lieux des plateformes crypto en France](https://www.cryptoreflex.fr/etudes/mica-juillet-2026-etat-des-lieux)
- [Avis OKX 2026 : frais, fiabilité et agrément MiCA](https://cryptoactu.com/avis/okx/)
- [Après Binance, quelle plateforme crypto agréée MiCA choisir en France ?](https://coinacademy.fr/academie/apres-binance-quelle-plateforme-crypto-agreee-mica-choisir-france/)
- [MiCA CASP tracker (registre ESMA)](https://casptracker.eu/)
