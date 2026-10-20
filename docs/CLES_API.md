# Créer des clés API OKX en sécurité

> Les menus d'OKX changent régulièrement : les noms ci-dessous peuvent différer légèrement.
> En cas de doute, cherche « API » dans l'aide d'OKX. **Les règles de sécurité, elles, ne changent pas.**

## Les 5 règles non négociables

1. **Jamais le droit de retrait** (*Withdraw*). Une clé qui ne peut que lire et trader ne peut pas vider ton compte vers l'adresse d'un voleur. Le bot **refuse de démarrer** si la clé a ce droit.
2. **Restriction par IP** quand c'est possible : la clé ne fonctionne que depuis l'adresse IP de la machine du bot. Une clé volée devient inutilisable ailleurs.
3. **Un sous-compte dédié** au bot pour le réel, avec seulement le budget que tu acceptes de perdre. Le bot considère tout le BTC et l'USDC du compte comme les siens.
4. **Les clés vont dans `.env`, nulle part ailleurs** : jamais dans le code, jamais dans un message, jamais sur une capture d'écran. `.env` est exclu de git.
5. **Clé compromise = clé supprimée immédiatement** sur OKX, puis recréée.

---

## Étape A — Clés du mode démo (testnet) : à faire maintenant

Le mode démo d'OKX utilise de faux fonds mais le vrai moteur de l'exchange : les ordres sont réellement envoyés, exécutés ou rejetés.

1. Connecte-toi sur okx.com (compte vérifié).
2. Passe en **mode démo** : menu *Trade* → *Demo trading* (un bandeau indique que tu es en démo).
3. Toujours en démo, ouvre ton profil → **API** → *Create API key* (clé « Demo trading »).
4. Renseigne :
   - **Nom** : `trading-bot-demo`
   - **Passphrase** : un mot de passe propre à cette clé (à noter, il ne sera plus affiché)
   - **Permissions** : **Read** + **Trade** uniquement. **Pas Withdraw.**
   - **IP** : optionnel en démo (obligatoire en réel, voir B).
5. Copie la clé, le secret et la passphrase dans `.env` (crée-le à partir de `.env.example`) :
   ```
   OKX_DEMO_API_KEY=...
   OKX_DEMO_API_SECRET=...
   OKX_DEMO_API_PASSWORD=...   # la passphrase
   ```
6. Approvisionne le compte démo en USDC fictifs si besoin (OKX propose de réinitialiser/recharger les fonds démo).
7. Vérifie, sans passer d'ordre :
   ```
   .\.venv\Scripts\python.exe -m bot check-keys
   ```
   Tu dois voir : permissions `read_only,trade` (sans `withdraw`), tes soldes démo, les minimums du marché et **tes vrais frais**.

## Étape B — Clés réelles : plus tard, seulement après des semaines de testnet sans anomalie

1. **Crée un sous-compte** : profil → *Sub-accounts* → *Create* (type standard). Transfère dessus **uniquement** le budget du bot (le défaut `execution.max_capital` est de 100 USDC).
2. Règle le sous-compte en **mode spot** (paramètres de trading → mode de compte « Spot » / simple) : pas de marge possible, même par erreur.
3. Sur le sous-compte : profil → **API** → *Create API key* :
   - **Permissions** : **Read** + **Trade**. **JAMAIS Withdraw.**
   - **IP** : l'adresse IP publique fixe de la machine du bot (le VPS de l'étape 7). Une box internet a souvent une IP qui change : c'est une raison de plus d'utiliser un VPS. OKX peut aussi désactiver les clés de trading **non liées à une IP** après une période d'inactivité.
4. Dans `.env` :
   ```
   OKX_API_KEY=...
   OKX_API_SECRET=...
   OKX_API_PASSWORD=...
   ```
5. Vérifie : `.\.venv\Scripts\python.exe -m bot check-keys --live` (aucun ordre).
6. Le réel ne démarre qu'avec `python -m bot trade --live` **et** la phrase de confirmation tapée au clavier.

## Si quelque chose ne va pas

| Symptôme | Cause probable |
|---|---|
| `Clés absentes` | `.env` absent, mal nommé ou variables vides |
| `REFUS : cette clé a le droit de RETRAIT` | recrée la clé sans *Withdraw* |
| `AuthenticationError` | clé/secret/passphrase erronés, ou clé démo utilisée en réel (ou l'inverse) |
| `PermissionDenied` / IP | l'IP de la machine ne correspond pas à celle liée à la clé |
| `order_too_small` dans le journal | ordre sous le minimum d'OKX (≈ 0,0001 BTC) : capital trop faible pour la taille calculée |
