# Notifications (read-only)

Ce module envoie des notifications **informatives uniquement** pour les signaux jugés intéressants.

## Garanties de sécurité

- Aucune notification ne peut exécuter un ordre.
- Aucun bouton/action d'exécution n'est inclus.
- Aucun token (Telegram/Discord/email) n'est stocké.
- Le trading live reste interdit (demo/paper only).

## Variables d'environnement

- `NOTIFICATIONS_ENABLED=false` (par défaut)
- `NOTIFICATION_CHANNEL=console` (canaux supportés: `console`, `file`, `console,file`)
- `ALERT_MIN_SCORE=70`

Si `NOTIFICATIONS_ENABLED=false`:

- aucune notification n'est envoyée,
- `reports/alerts.log` n'est pas écrit,
- les logs standards de l'application restent inchangés.

## Déclencheurs d'alerte

Une alerte est émise si au moins une condition est vraie:

- `score >= ALERT_MIN_SCORE`
- `pattern_score > 0`
- `status=watchlist`
- `status=detected`
- `near_miss` détecté
- symbole passant de `off_hours` vers une session tradable

## Format d'une alerte

Chaque alerte contient:

- `timestamp_utc`
- `asset_class`
- `logical_symbol`
- `mt5_symbol`
- `setup`
- `status`
- `score`
- `risk_reward`
- `pattern_score`
- `detected_patterns`
- `session_name`
- `reasons`
- `broker`
- `mode`
- `safety_status`

## Architecture extensible

Le module expose des abstractions de canal pour permettre l'ajout futur de:

- Telegram
- Discord
- email

Sans configuration obligatoire actuelle: ces canaux restent des placeholders non bloquants.
