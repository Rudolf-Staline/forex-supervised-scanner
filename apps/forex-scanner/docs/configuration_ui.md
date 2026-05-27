# Configuration UI

Le tableau de bord Streamlit de Forex Supervisor inclut un onglet **Configuration**. Cet onglet vous permet de visualiser et de modifier certains paramètres de l'application sans avoir à éditer manuellement les fichiers JSON.

## Lancement de l'interface

Assurez-vous que l'environnement virtuel est activé, puis lancez :

```bash
cd apps/forex-scanner
streamlit run app/ui/streamlit_app.py
```

Allez ensuite sur l'onglet **Configuration** depuis votre navigateur.

## Sections

1. **Mode Général :** Affiche la configuration actuelle (provider, broker mode) ainsi que la liste des symboles couverts par le scanner.
2. **Variables de Sécurité (.env) :** Affiche l'état des variables d'environnement telles que `EXECUTION_MODE`, `ALLOW_LIVE_TRADING`, etc. Ces paramètres sont affichés en lecture seule. Un système de "badge" évalue le niveau de risque global. Si la configuration est identifiée comme dangereuse (`DANGEROUS`), les fonctionnalités sensibles de l'interface seront bloquées.
3. **Adaptive Thresholds :** Permet d'activer ou désactiver les seuils adaptatifs, et de paramétrer finement leurs limites (`hard_floor`, `hard_cap`). Vous pouvez générer un rapport simulé en un clic pour valider la configuration.
4. **Rapports (Read-Only) :** Fournit des boutons rapides pour lancer des audits de sécurité et des vérifications, comme `safety_env_doctor.py` et `audit_integrity.py`.
5. **Export / Import :** Sauvegardez la configuration globale en JSON pour archivage, ou importez un nouveau fichier de configuration.

## Limites de Sécurité (Garde-fous)

Pour assurer que le scanner reste toujours sous contrôle (mode paper/demo) :

- L'UI ne modifie jamais directement le fichier `.env`.
- Les options telles que `ENABLE_DEMO_EXECUTION` et `ALLOW_MULTI_ASSET_DEMO_TRADING` ne peuvent pas être activées via l'interface.
- L'import d'un fichier de configuration modifiant des variables liées au `live_trading` est systématiquement intercepté et bloqué.
