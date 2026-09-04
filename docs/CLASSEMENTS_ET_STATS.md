# Classements et statistiques : la forme de la réponse est connue

`server/fifa14_blaze_server.py` porte, depuis le 22 août, un commentaire qui dit
ce qui manquait :

> Le jour où il y aura des lignes à rendre, il faudra la forme exacte de la
> réponse — que la table de réflexion du titre détient et qu'on n'a pas pu lire,
> cette console ne supportant qu'environ 300 Ko de `getmem` avant de tomber du
> réseau.

Elle est là. Le SDK Blaze généré d'Impulsum14 la contient, et se lit sans
toucher à la console. `docs/blaze-sdk-reference.json` en est la table aplatie :
23 composants, 386 commandes, 814 structures, 2 865 membres.

## Le numéro qu'on cherchait à l'aveugle

`stats_notification_sweep` existe parce qu'on ne savait pas sous quel numéro de
notification se clôt un appel `...Async` : l'expérience envoyait la même charge
utile sous vingt-quatre numéros à la fois, en espérant que l'un prenne.

Le composant Stats n'en a que deux, et elles sont nommées :

| n° | notification | charge utile |
|---|---|---|
| **50** | `GetStatsAsyncNotification` | `KeyScopedStatValues` |
| **51** | `GetLeaderboardTreeNotification` | `LeaderboardTreeNode` |

## Les structures

`KeyScopedStatValues` — ce que `getStatsByGroupAsync` doit finir par envoyer :

    0  GRNM  String   GroupName        le nom du groupe demandé, réécho
    1  KEY   String   KeyString
    2  LAST  Bool     Last             dernier envoi de la série
    3  STS   Struct   StatValues
    4  VID   UInt32   ViewId           réécho du VID de la requête

`StatValues` porte deux listes : `AGGR` d'`EntityStatAggregates`, `STAT`
d'`EntityStats`. Et une `EntityStats` est l'entité et ses colonnes :

    0  EID   Int64       EntityId
    1  ETYP  ObjectType  EntityType
    2  POFF  Int32       PeriodOffset
    3  STAT  List        StatValues     les colonnes, en chaînes

`LeaderboardStatValuesRow` — une ligne de classement :

    0  ENAM  String  EntityName     le nom affiché
    1  ENID  Int64   EntityId
    2  RANK  Int32   Rank
    3  RSTA  String  RankedStat     la valeur sur laquelle on classe
    4  RWFG  Bool    IsRawStats
    5  RWOT  List    OtherRawStats
    6  RWST  Union   RankedRawStat
    7  STAT  List    OtherStats
    8  UATT  UInt64  Attribute

`LeaderboardTreeNode` — un nœud de l'arbre que `getLeaderboardTreeAsync` refuse
aujourd'hui :

    0  CHDE  UInt32  Next2Last      1  CHDS  UInt32  FirstChild
    2  LAST  Bool    LastNode       3  NAME  String  NodeName
    4  NDID  UInt32  NodeId         5  RTNM  String  RootName
    6  SDES  String  ShortDesc

Et la requête `getCenteredLeaderboard`, pour savoir ce qu'on nous demande :
`CENT` (l'entité autour de laquelle centrer), `COUN` (combien de lignes),
`LBID`/`NAME` (quel tableau), `POFF`, `BOTT`.

## Ce que ça ne règle pas

Le refus actuel reste **exact**. Un classement est un agrégat de matchs et ce
serveur n'en a aucun à agréger ; répondre une erreur est ce qui rend l'écran
utilisable, un succès vide est ce qui le fige — vérifié sur la console le
22 août. Connaître la forme ne crée pas les données.

Ce que ça change : le jour où il y aura des matchs à agréger, il n'y aura plus
rien à deviner, et le balayage de notifications n'a plus lieu d'être.

## Les commandes réellement sans réponse

Le journal du VPS est cumulatif : compté brut, il désigne vingt-quatre commandes
sans réponse, dont douze ont été implémentées depuis. Mesuré contre le code
d'aujourd'hui, il en reste deux qui portent un nom :

| vues | commande |
|---|---|
| 10 | `GameManager::setPlayerAttributes` (4/8) |
| 1 | `CommerceInfo::getProductAssociation` (24/5) |

Le reste — composants 0, 8, 49, 110, 113, 164, 256, 320, 2252, avec des numéros
de commande comme 65363 ou 27265 — ne sont pas des commandes Blaze. Le port
10041 est ouvert sur Internet et les scanners le trouvent.

## Ce qui a été ajouté pour la prochaine session

`unknown_route` n'enregistrait que les deux numéros. Il enregistre maintenant
les champs de la requête et la trame brute : une commande sans réponse laisse
désormais derrière elle de quoi être répondue — les noms de groupe, les
identifiants de tableau, les types d'entité que le titre veut vraiment — au lieu
d'une liste de choses à deviner.
