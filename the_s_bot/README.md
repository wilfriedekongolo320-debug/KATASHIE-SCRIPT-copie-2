# Bot Telegram de surveillance des revendeurs (mode lecture seule)

Ce bot est une version de `Thes.py` limitée à la surveillance des revendeurs. Il lit `config.json` et `resellers.json`, n'exécute aucune commande système, et ne crée, ne modifie ni ne supprime aucun compte.

## Fonctions

- `/start` : aide et confirmation du mode lecture seule.
- `/resellers` : liste des revendeurs et temps restant.
- `/status` : résumé des abonnements actifs, proches de l'expiration ou expirés.
- `/check` : lance une vérification immédiate.
- Notifications envoyées uniquement au `super_admin` configuré : nouvel ajout/retrait, échéances à moins de 24 h ou de 6 h, expiration et démarrage du service.
- Les commandes Telegram sont réservées au super administrateur.

Les alertes sont dédupliquées dans `/var/lib/the-s-bot/state.json`. L'échéance est calculée depuis `added_datetime` (ou `added_date`) et `max_hours` ; pour les anciens formats, les valeurs par défaut sont de 48 heures pour `trial` et `max_days` (30 par défaut) pour les autres types. Les champs `expires_at` et `expiry_datetime` sont aussi reconnus.

## Installation sur le VPS

Depuis le dépôt cloné :

```bash
sudo bash the_s_bot/install.sh
```

L'installateur demande le token Telegram et l'ID du super administrateur si `/etc/the_s_bot/config.json` n'existe pas. Il installe un environnement virtuel, copie le bot sous `/etc/the_s_bot/Thes.py`, puis active `the-s-bot.service`. Le fichier existant `/etc/the_s_bot/resellers.json` est conservé ; s'il est absent, un fichier vide est créé.

Pour installer sans saisie interactive, fournir `THE_S_BOT_TOKEN` et `THE_S_BOT_SUPER_ADMIN` dans l'environnement avant de lancer le script. Pour vérifier le service :

```bash
sudo systemctl status the-s-bot.service
sudo journalctl -u the-s-bot.service -f
```

Le service tourne sous l'utilisateur dédié `the-s-bot`, avec accès en lecture aux fichiers de configuration et écriture limitée à son état de surveillance.
