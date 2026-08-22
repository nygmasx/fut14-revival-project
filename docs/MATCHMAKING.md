# Le matchmaking en ligne

Le 21 août 2026, une console est entrée dans **Face-à-Face** et a envoyé la
première trame que ce serveur ait jamais reçue sur le composant 4. À la fin de
la journée, elle créait une partie, s'y trouvait elle-même, **fabriquait sa
propre session XNet** et rapportait l'état du maillage pair par pair.

Ce document dit ce qu'on sait, comment on l'a su, et ce qui reste.

## En une phrase

Le match ne passe pas par ce serveur. `NTOP = 130` est
`PEER_TO_PEER_FULL_MESH` : les deux consoles se parlent directement, et le
serveur n'a qu'un seul rôle — **les présenter, puis décider quand elles se
sont trouvées**.

## La méthode, qui vaut plus que le résultat

Le serveur écrit `unknown_route` chaque fois que le titre demande un composant
et une commande sans gestionnaire. **Le jeu tient donc lui-même la liste de ce
qu'il reste à écrire.** Tout ce qui suit est sorti de là, jamais d'une
supposition sur ce qu'un client pourrait vouloir.

Deuxième règle, apprise à ses dépens : les numéros de commandes et les tags
viennent du **binaire de FIFA 14**, pas des tables publiées pour d'autres jeux
Blaze. Elles diffèrent en sept endroits. Et là où le binaire ne dit rien, on
n'invente pas — on l'écrit comme non lu.

## La séquence, telle qu'elle se déroule

```
console                          serveur
   |                                |
   |-- 4/13 startMatchmaking ------>|   critères, durée 20 s, topologie 130,
   |                                |   et PNET : son XNADDR
   |<----- réponse { MSID } --------|   un identifiant de session non nul
   |<----- 4/12 statut async -------|
   |                                |
   |            ... 20 secondes ... |
   |                                |
   |<----- 4/20 NotifyGameSetup ----|   GAME (36 membres), PROS (le roster),
   |                                |   REAS index 3, LFPJ, QUEU
   |<----- 4/71 hôte de plateforme -|
   |<----- 4/30 join terminé -------|
   |<----- 4/100 état -> PRE_GAME --|
   |                                |
   |-- 4/15 finalizeGameCreation -->|   XNNC (nonce) + XSES (XSESSION_INFO)
   |<----- 4/115 session mise à jour|   renvoyés tels quels
   |                                |
   |-- 4/29 updateMeshConnection -->|   par pair : « je le vois » / « non »
   |<----- 4/100 état -> IN_GAME ---|   quand tout le roster s'est vu
```

Une recherche que personne ne remporte se termine par `4/10
NotifyMatchmakingFailed` avec `RSLT = 3` (`SESSION_TIMED_OUT`), et le jeu
dessine alors son propre écran « aucun adversaire ».

## Les trois pièges qui ont coûté cher

### La notification 10 n'est pas ce que disent les tables publiques

En Blaze 2 elle s'appelle `NotifyMatchmakingFinished` et porte les deux
issues. Dans FIFA 14 c'est **`NotifyMatchmakingFailed`** et elle ne porte que
l'échec — le succès arrive comme un `NotifyGameSetup`. L'envoyer pour annoncer
un match aurait terminé la recherche au lieu de la remporter.

### Une union dans une liste n'a pas la même forme qu'une union dans un champ

`HNET` est déclarée comme une liste de **structs**, mais ses éléments sont des
unions `NetworkAddress`, et une union commence alors par un octet donnant le
membre actif — **sans enveloppe `VALU`**, contrairement à la même union placée
dans un champ.

Le décodeur lisait cet octet comme « struct vide » et se désynchronisait. La
première trame `createGame` de l'histoire du projet a tué la connexion Blaze.

La règle qui tranche n'est pas une heuristique : **le premier octet d'un tag
TDF vaut toujours ≥ 0x80**, parce que `encode_tag` place `(0x20 | c & 0x1F)`
dans les six bits de tête. Un indice de membre d'union est un petit nombre.
Les deux domaines sont disjoints. Sous cette règle la trame décode 611 octets
sur 611 et se ré-encode à l'octet près, et les 852 listes de structs déjà
présentes dans les captures décodent identiquement.

### Les tables de membres sont à moitié écrites au démarrage

C'est le piège qui a coûté le plus longtemps. La table de réflexion de
`ReplicatedGameData` semblait contenir quatorze membres. Elle en contient
**trente-six** : les autres sont écrits à l'initialisation par du code qui
**assemble chaque tag à partir d'une paire d'instructions** :

```
lis  r11, 0xA339
ori  r11, r11, 0x7300     ->  0xA3397300 = "HSES"
stw  r11, 272(r31)        ->  table, entrée 17
```

Chercher les tags comme mots alignés dans l'image ne pouvait jamais les
trouver. `PGSC` et `RGID` circulaient sur le fil pendant qu'on les croyait
inexistants — notre propre capture le prouvait.

## Le champ qui débloquait tout

Pendant des heures, la console recevait six trames, n'émettait aucune erreur,
ne se déconnectait pas, et **continuait à afficher son spinner**. Un décodeur
TDF ignore ce qu'il ne connaît pas et met des valeurs par défaut au reste :
une notification à moitié comprise se lit aussi bien qu'une complète.

`ReplicatedGamePlayer` fait **dix-huit** membres, pas seize. Le manquant qui
comptait :

> **`UID` → `mPlayerSessionId`**

C'est par là qu'un client **se reconnaît lui-même** dans un roster. Sans lui il
obtient une partie où il ne trouve aucun joueur local, et il jette le setup —
sans erreur et sans un mot.

Une seconde après l'avoir ajouté, la console envoyait `finalizeGameCreation`.

## Le résultat, le 22 août à 00 h 41

La séquence entière, sur du vrai matériel, en vingt et une secondes :

```
00:41:07  4/13  startMatchmaking          ->  { MSID }  puis 4/12
00:41:27  4/20  NotifyGameSetup               partie, roster de deux joueurs
          4/71  hôte de plateforme
          4/30  l'hôte a fini de rejoindre
          4/100 état -> PRE_GAME
          4/21  Sparring arrive
          4/30  Sparring a fini de rejoindre
00:41:28  4/15  finalizeGameCreation      <-  XNNC 16 o, XSES 256 o
          4/115 session mise à jour       ->  renvoyés tels quels
          4/29  maillage : connecté à lui-même
          4/100 état -> IN_GAME
          4/29  maillage : Sparring injoignable
```

Et à l'écran : **« Votre adversaire a quitté la partie. »**

C'est la première fois que ce projet obtient un message de *football* plutôt
qu'une erreur de protocole. Le titre est entré dans le match, a composé le
numéro de son adversaire, n'a trouvé personne — parce qu'il n'y a personne à
`192.168.1.200` — et en a tiré la seule conclusion possible.

**Autrement dit : tout ce que Blaze pouvait faire est fait.** Ce qui reste
n'est plus du protocole, c'est une deuxième console.

## Ce que la console fabrique elle-même

`XNNC` (16 octets) et `XSES` (**256 octets**) ne sont **pas au
serveur de les inventer**. L'hôte les construit sur son propre matériel et les
remet dans `finalizeGameCreation`. Le serveur les garde et les renvoie — et
c'est **exactement le blob dont une deuxième console a besoin** pour composer
le numéro de la première.

C'est aussi pourquoi ils ne sont pas envoyés dans le `NotifyGameSetup`
initial : en mettre des vides serait prétendre savoir.

Sur la taille de `XSES` : la littérature décrit un `XSESSION_INFO` de 60
octets (XNKID + XNADDR de l'hôte + XNKEY). Ce que la console envoie en fait
**256**, dont 146 non nuls, avec un motif qui se répète à la moitié — donc
deux blocs de 128 plutôt qu'une structure unique. Ce n'est pas décodé et ce
n'est pas nécessaire de le décoder : le serveur le relaie tel quel.

## Le décor : Xbox LIVE

Le trafic pair-à-pair passe par le transport sécurisé XNet, qui a besoin des
adresses fournies par Xbox LIVE. Le correctif `xnet_nosecure` de ce projet ne
lève pas cette dépendance — et `XNET_STARTUP_BYPASS_SECURITY` est réservé aux
kits de développement, donc probablement inerte sur du matériel retail.

Ce qui joue en notre faveur : le XNADDR que la console envoie a un `inaOnline`
**non nul** et un bloc `abOnline` rempli, ce qui est la signature d'une console
vue comme connectée à LIVE. Et **ce projet ne redirige rien de Xbox LIVE** —
le hook `connect` filtre par port : 10041, 42124/26/27, 18080, 8094, 8080. Que
du EA.

C'est donc l'architecture qui a déjà fonctionné ailleurs sur du matériel
retail : **LIVE réel pour la couche console, serveur privé pour la couche EA
morte.**

## Deux consoles, sans navigateur de parties

Le plus court chemin pour un vrai match n'est pas la liste des parties
disponibles : c'est **deux recherches simultanées**. Les deux consoles envoient
`startMatchmaking` avec leur propre XNADDR, donc le serveur a les deux
adresses et n'a plus qu'à les mettre dans une partie et donner à chacune celle
de l'autre.

**Celle qui attendait déjà héberge.** Ce n'est pas arbitraire : l'hôte est
celui dont l'autre compose la session XNet, et celui qui attendait attendait
parce qu'il n'y avait personne — c'est donc lui qu'il faut appeler.

L'autre reçoit la notification **22** au lieu de la **20**. Les deux portent la
même classe de charge utile — ce binaire ne contient qu'un seul
`NotifyGameSetup` — mais 22 est ce qui fait *composer* un client au lieu
d'attendre qu'on l'appelle. Ce routage est la convention de la famille, pas
une lecture du code de dispatch du client : **si un seul côté se connecte, les
inverser est la première chose à essayer.**

La compatibilité se juge sur `GVER`. Deux versions qui ne s'accordent pas
échoueraient dans la couche réseau pour une raison qui n'a rien à voir avec le
réseau.

### `joinGame` (commande 9)

L'autre porte d'entrée. Le joueur qui rejoint apporte tout : son `PNET` et son
`XSES` voyagent dans la requête, donc rien de la deuxième console n'a besoin
d'avoir été mis en cache. La réponse fait **quatre** membres — `GID`, `JEX`,
`JGS`, `REX` — et non les deux des tables publiées.

### Le navigateur de parties, et pourquoi il attend

`getGameListSnapshot` (100) et `getGameListSubscription` (101) ne renvoient
**aucune partie** : seulement un identifiant de liste et un nombre. Les parties
arrivent par la notification 201. Et leurs entrées ne sont pas des
`ReplicatedGameData` mais des **`GameBrowserGameData`**, une classe distincte
de 27 membres qui ajoute `HOST`, `PCNT`, `ROST`, `TINF` et omet tout ce qu'un
joueur n'a besoin de connaître qu'après s'être engagé.

Rien de tout ça n'est nécessaire à deux consoles qui cherchent en même temps.

## Les modes FUT en ligne

Ils passent **par le même composant 4**, à travers une couche d'adaptation
générique : `OSDK_MatchupAdaptor` expose `QuickMatch`, `CustomMatch`,
`CreateSession`, `JoinSessionByRowIndex`. Les exports de CardsDLL correspondent
un pour un — `ServiceQuickMatch`, `ServiceCreateSession`, `CancelMatch` — et
`AddFUTMatchmaking` est la couche FUT qui **ajoute ses propres critères à une
requête générique**. C'est-à-dire `ATTR` et `CRIT`, exactement les attributs
`gameType0` / `fifaMatchupHash` / `fifaTeamLevel` que notre propre capture de
`createGame` porte déjà.

**Donc tout le travail sur le composant 4 est ce sur quoi tourneront les
saisons et les tournois FUT en ligne.**

### Les saisons en ligne n'ajoutent rien

Il n'y a **ni composant ni adaptateur de saisons**. Le jeu a
`OSDK_MatchupAdaptor` et `OSDK_TournamentAdaptor`, et rien d'autre. Une saison
en ligne FUT, c'est donc **la couche de matchup partagée pour le match** —
celle qu'on vient de construire — **et l'API HTTP de FUT pour le classement**,
que ce serveur sert déjà à `/ut/game/fifa14/season/user`.

### Les tournois non plus, ou presque

`OSDKTournaments` est le composant **2271**, et le titre l'implémente. Mais
`OSDK_TournamentAdaptor` expose `CancelQuickMatch` à côté de `GetMemberTree`,
et le binaire porte une clé de configuration `OSDK_TOURNAMENT_QUICKMATCH_TIMEOUT`.
Autrement dit : **les matchs de tournoi passent par le même chemin quickmatch**,
et 2271 ne tient que le tableau.

Et 2271 n'a **jamais envoyé une seule trame** à ce serveur. FUT utilise la
route HTTP `/ut/game/fifa14/tournament/user`, déjà servie. À ne pas construire
tant qu'une console ne la demande pas.

## Les composants, par leur numéro

Lus dans les constructeurs du client — chacun fait `li rD,<id>` puis
`sth rD,8(r3)` à côté de son vtable. Les seize que ce dépôt connaissait déjà
sont tous ressortis justes.

| | | | |
|---|---|---|---|
| 1 Authentication | 10 CensusData | 25 AssociationLists | 2249 OSDKSettings |
| 4 GameManager | 11 Clubs | 27 GpsContentController | 2252 OsdkArena |
| 5 Redirector | 14 Mail | 28 GameReporting | 2268 OsdkOnlinePass |
| 6 Playgroups | 15 Messaging | 35 Authentication2 | 2270 OSDKDigitalDownloadPreview |
| 7 Stats | 21 Rooms | 2069 FifaCups | 2271 OSDKTournaments |
| 9 Util | 24 CommerceInfo | 2070 CoopSeason | 30722 UserSessions |
| | | 2076 SponsoredEvents | |
| | | 2077 Easfc | |

`20`, `2000`, `2148`, `30720`, `30721`, `30723`, `30725` et `30726` sont
**annoncés et pourtant absents du client** — pas de constructeur, pas de vtable,
pas de nom, pas de trafic. C'étaient des composants du serveur d'EA.

Et la liste annoncée n'est pas une barrière : le client martèle 2076 cent mille
fois, et 2076 n'y figure pas.

### CardHouse (2148) n'existe pas dans ce jeu

Ce dépôt porte des constantes `CARDHOUSE_*` et leurs gestionnaires depuis le
début. Ils viennent de HUT, le mode de NHL, via le dump BlazeSDK de Zamboni —
comme le dit d'ailleurs la docstring du module à propos de ses formats.

Quatre vérifications concordent : aucun constructeur de composant Blaze dans
`default.xex` ni dans `CardsDLLzf`, aucune chaîne `CardHouse` ou
`gamerGetInfo` dans l'un ou l'autre, aucun vtable — et surtout, **le composant
2148 n'est jamais apparu dans une seule trame** de tous les journaux de ce
dépôt. Ses seules occurrences sont dans la liste annoncée.

C'est du code mort. Il est laissé en place — le retirer ne prouverait rien —
mais personne ne devrait le lire comme une connaissance sur ce jeu.

## Ce qui reste

- **Deux vraies consoles.** Tout ce qui précède a été obtenu avec une seule et
  un adversaire inventé (`FIFA14_TEST_OPPONENT`, désactivé par défaut). Le
  maillage ne peut pas se fermer contre quelqu'un qui n'existe pas : la
  console rapporte `DISCONNECTED`, ce qui est la bonne réponse.
- ~~**`joinGame` (commande 9)**~~ — fait le 22 août, voir plus bas.
- **Les modes FUT en ligne** — on ne sait pas encore s'ils passent par
  GameManager ou par l'API web FUT.

## Fragilité de la console, à savoir avant de toucher à XBDM

Cette console est tombée du réseau trois fois dans la journée. À chaque fois
il a fallu le bouton d'alimentation.

- **La capture d'écran seule n'est pas coupable** : une capture prise avec le
  surveillant de patch arrêté a été suivie d'un XBDM parfaitement sain.
- **Le surveillant de patch** balaie 4 à 8 Mo de mémoire en boucle. Il tournait
  quelques secondes avant deux des trois chutes.
- **L'accroche de manette virtuelle** écrit dans le code du titre à chaque
  `apply`/`restore`. Cinq cycles enchaînés ont précédé la troisième chute
  immédiatement.

Donc : arrêter `fut-patch-watch` avant toute lecture XBDM, et ne pas enchaîner
les cycles d'accroche manette.


## 22 août : deux vraies consoles se maillent

Ce jour-là, deux consoles — une en France, une en Algérie — sont entrées dans
la même partie et se sont vues. `mesh_complete`, `synthetic: false`. C'est le
mur contre lequel la veille avait buté.

Rien de ce qui a débloqué ça n'était dans le réseau. Les trois causes étaient
dans le serveur, et chacune ressemblait à un problème de connectivité.

### Le chemin de l'invitation n'est pas celui du matchmaking

La console invitée envoie un `joinGame` qui dit :

    GID  = 0
    SLOT = 1
    USER = { ID: <l'identifiant de l'hôte>, ... }

`GID` à zéro n'est pas une partie introuvable : c'est le client qui dit qu'il
ne raisonne pas en numéro de partie. Il a pris son hôte dans sa liste d'amis,
pas dans une liste de salons, et il le désigne par son identifiant de joueur.
Le serveur cherchait `games[0]` et répondait « aucune partie » à une demande
parfaitement valide.

Corollaire : un joueur qui *cherche* rejoint désormais une partie déjà créée
qui l'attend. L'écran de Face-à-Face a deux portes — « rechercher » et « créer
un match » — et deux joueurs qui prennent la seconde s'attendaient chacun seul
dans son salon. Un serveur dont le comportement dépend du bouton choisi est un
serveur qui a tort.

### Le champ `RRST`, et pourquoi on ne le comprend toujours pas

Ce `joinGame` porte `RRST`, un dictionnaire de chaînes vers structs. Sa
grammaire nous échappe : le champ occupe douze octets là où notre lecture n'en
consomme que dix, et les trois captures qu'on possède sont **identiques**, donc
elles ne permettent pas de trancher. Rien n'est deviné ici.

Ce qui est fait, c'est une resynchronisation, sous deux conditions strictes :

1. le reste doit se décoder **exactement** jusqu'au dernier octet ;
2. toutes les étiquettes retrouvées doivent être de vrais tags.

Sur cette trame, quatre décalages satisfont la première condition. Un seul
satisfait la seconde — les autres rendent `]@TA` ou `@P`, qui ne peuvent pas
être des tags. Sans ce second critère on aurait pris le premier venu.

La trame reprend à 0x7D, et le tout se lit : `USER` est retrouvé, c'est-à-dire
exactement l'objet de la requête. Une deuxième capture au contenu différent
suffirait à établir la grammaire pour de bon.

### Répondre à une requête ne demande pas de la comprendre

`response_frame` redécodait la requête **entière**, en mode strict, pour en
tirer trois nombres qui tiennent dans les douze premiers octets. Résultat : le
joueur rejoignait vraiment — le journal montre `player_joined` — puis la
construction de la réponse relisait la trame et l'exception fermait la
connexion. Sur la console : « Cette session de jeu n'existe plus. »

Le journal disait littéralement `player_joined` suivi de `connection_error`.

### Un état de partie appartient à la partie

`mesh_progress` rendait le passage en `IN_GAME` à son appelant — la console
dont le rapport de maillage complétait le tableau. À 16:10:31, le maillage
s'est bouclé entre deux vraies consoles, la partie est passée en jeu, et
**seule celle qui avait parlé en dernier l'a su**. L'autre est restée sur son
écran de chargement, à attendre un signal qui ne lui était pas adressé.

### Ce qu'on a appris sur la méthode

Les trois symptômes — « serveur momentanément indisponible », « cette session
de jeu n'existe plus », « ça charge encore » — ressemblaient tous à des
problèmes de réseau. Aucun ne l'était.

La comparaison qui a tranché : les octets de `NotifyGameSetup` envoyés lors
d'un appariement qui marchait (15:05) et d'un qui ne marchait pas (15:50)
étaient **identiques** — 832 octets, mêmes champs, 34 membres. Ça a écarté le
serveur d'un coup, et fait chercher ailleurs.

Et une règle qui s'est vérifiée deux fois : quand un décodeur meurt sur une
trame, ce n'est pas le décodeur qui est de trop, c'est le fait qu'il soit
fatal. Une trame Blaze est une suite de champs indépendants ; ne pas savoir
lire le septième ne rend pas les six premiers faux.
