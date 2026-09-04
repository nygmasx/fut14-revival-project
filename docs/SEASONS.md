# Seasons: three shapes, three failures

Unsolved. `/ut/game/fifa14/season/list` answers `{"seasons":[]}` and
`/season/user` answers `{}` unless `FIFA14_SEASON_MODE=native` is set.

Empty is not a solution. It is the only answer that has never broken anything,
and it is what the PC revival carries here for the same reason.

## What was tried

**1. Invented members.** `division`, `matchesPlayed`, `matchesToPlay`,
`pointsToPromote`, `lost`, `coinsPerWin`, `trophiesWon`, plus a
`relegated`/`promoted` boolean pair. None of the nine appears anywhere in
CardsDLL's JSON member-name table, so the parser could not read one of them.
The mode opened and showed constructor defaults — no division, no record, no
reward. This was never actually observed on the console; it is what the code
served for weeks.

**2. Correct names, wrong structure.** Each name above replaced with one the
table carries: `divisionId`, `numMatches`, `thresholdPoint`, `seasonCoins`,
`gamesPlayed`, `seasonGamesLost`, `seasonTitlesWon`. The console answered

> Les saisons ne sont pas disponibles pour le moment. Veuillez réessayer plus
> tard.

The lesson is worth stating plainly, because checking the names felt like
diligence: **a name table proves a member exists, not where it lives.**
`thresholdPoint` is real — it lives inside `prizeSet`, not at the top level.
Every one of those nine names was verified present and the document was still
wrong.

**3. The full native record.** Taken from an independently built PC revival of
the same game, whose season record nests the fixture list in `matches` and the
rewards in `prizeSet`, both arrays of records — the same fault as a cup's
`rounds` served as a count, one level deeper. All 28 of its members were
checked against the Xbox name table and all 28 are present.

The FUT loader froze on entering the mode.

## What the freeze looked like

    13:17:21  GET /ut/game/fifa14/season/list      served
    13:17:21  GET /fut/items/xbl2/-1.json          x10, one per division
    13:17:21  GET /fut/items/images/trophies/xbl2/item.big
    13:17:21  GET /ut/game/fifa14/season/user      served
              nothing further

So the freeze is after both documents are served, in whatever the screen builds
from them. The console itself stayed healthy — XBDM kept answering and the
title kept running; only the FUT frontend hung.

`trophyResourceId: -1` is one confirmed mistake in that attempt. The PC build
uses -1 as a "no trophy" sentinel and notes that 0 made its client perform ten
meaningless item-0 lookups. On Xbox, -1 does exactly what 0 does: ten lookups
of `/fut/items/xbl2/-1.json`. A value proven on one platform is not evidence
for the other — which is the same reasoning that was applied correctly to the
member names in the same sitting, and then not applied here.

## What to try next

Reduce rather than guess. A freeze gives no error to read, so the only way
through is one variable at a time:

1. one division, no `matches`, no `prizeSet` — does the screen open?
2. add `prizeSet` alone;
3. add `matches` alone;
4. then both, then the remaining nine divisions.

Each step costs a server restart and a mode entry. Serving the whole record at
once, as was done here, produces a freeze and no information.

Also open: whether `trophyResourceId` should simply be absent rather than
carrying any number, and what `season/user`'s `round` should be for a season
never played — the PC build sends wire 1 for the first fixture and warns that
wire 0 becomes the client's 0xFFFF sentinel.


## Attempt four, 13 August: the asset chain was not it

Two real defects sat on the seasons entry path and both were fixed the night
before, without knowing they were on it:

* `/fut/items/xbl2/-1.json` was answered with the blanket `{"itemData":[]}`,
  because the route matched digits only and the seasons screen asks for a
  negative id -- once per division, ten times. An empty definition is what
  makes the console build `trophies/xbl2/.big` with no basename.
* the empty BIG archive declared its own size big-endian, so sixteen bytes
  announced themselves as 0x10000000 -- 268 megabytes.

Both are visible in this attempt's journal as fixed: the ten `-1.json` requests
each come back with 151 bytes and a real `assetName`, and the console then asks
for `trophies/xbl2/**item**.big` rather than `.big`.

**The screen froze anyway**, in the same place:

    00:31:51  GET season/list                served
    00:31:51  GET /fut/items/xbl2/-1.json    x10, served
    00:31:52  GET trophies/xbl2/item.big     served
    00:31:52  GET season/user                served
              nothing further

So the asset chain is eliminated. What is wrong is in the record, which is what
the reduction ladder below is for. The console stayed healthy throughout --
XBDM answering, title running, only the frontend hung.

## The ladder, reachable by name

`FIFA14_SEASON_MODE` now takes five values rather than two:

    empty     no seasons at all -- the only answer known to break nothing
    minimal   one division, no `matches`, no `prizeSet`
    prizes    minimal plus `prizeSet`
    matches   minimal plus `matches`
    native    every division, both arrays -- the shape that freezes

One rung per relaunch, one entry into the mode each. `minimal` first: if that
opens, the record's frame is right and one of the two arrays is the fault; if
it freezes too, the fault is in the record's own members and neither array
matters yet.

## Résolu — 13 août 2026

Le mode s'ouvre, se joue, et se souvient. Trois défauts distincts, dans cet
ordre.

### 1. `divisionId` est un rang, pas un numéro

La bissection de l'échelle ci-dessus a nommé le membre en cinq relances :

    {}                                       ouvre, et tient
    {"seasonId":1}                           ouvre, et tient
    {"seasonId":1,"divisionId":10}           ouvre, puis gèle
    {"seasonId":1,"divisionId":10,"round":1} gèle
    {"seasonId":1,"divisionId":0}            ouvre, tient, propose de démarrer

Ce n'est pas « nommer une division que la liste ne contient pas » : le disque
servi à côté portait `divisionId` 10 lui-même. Ce que 10 est aussi, sur une
liste de dix, c'est un cran au-delà du dernier index. Le client lit ce membre
comme la **position** du disque dans la liste servie, comptée depuis zéro.

Le piège tient à ce que les deux suites vont en sens inverse : les ids de
disques montent de 1 à 10 pendant que les numéros de division descendent de 10
à 1. `divisionId: 0` et `division 10` désignent la même chose.

Avec ça, la liste complète — dix divisions, quatre matchs et quatre
récompenses chacune, 12 590 octets — s'ouvre sans broncher.

### 2. La route de sauvegarde est un cran plus profonde

La table de modèles d'URL porte `ut/%s/season/%%s/user` sous
`SEASONUSER_ALTER`, et lire ce `%%s` comme l'id de la saison est faux. La
chaîne de format qui sert à le construire est ailleurs, à côté du sérialiseur
de saison : `%d/division/%d`. Ce qui part sur le fil est donc

    PUT /ut/game/fifa14/season/1/division/10/user
    GET /ut/game/fifa14/season/user/history?type=offline

Les deux tombaient sur le 404 générique. Le numéro de division dans le chemin
est bien le **numéro**, pas la position : le client relit `divisionId` dans le
disque qu'il a choisi, ce qui fait que la position 0 devient `division/10`. Les
deux sens coexistent, chacun à sa place.

Le corps est celui des coupes avec un mot changé — `.rdata` épelle le blob
`data` pour les saisons contre `tournamentData` pour les coupes, avec la même
queue `progressData`. `SeasonProgress` est donc `TournamentProgress` clé par
une paire, et il hérite de la règle que les coupes ont payée : une saison
sauvegardée au round 1 avec un blob vide n'a pas de premier match à reprendre,
et se répond « pas de saison ».

### 3. `season/user` mentait sur l'avancement

Le client sauvegarde sa propre progression après chaque match — round 2 dès le
premier match abandonné. `season/user` répondait round 1 quoi qu'il arrive,
donc rentrer dans le mode proposait dix matchs sur dix quel qu'en soit le
nombre déjà joués. Il lit maintenant la saison sauvegardée.

### Ce que la console a fait, une fois les trois corrigés

    13:37:17  GET  season/list                    12 590 o, dix divisions
    13:37:18  GET  season/user                    {"seasonId":1,"divisionId":0,"round":1}
    13:37:48  PUT  season/1/division/10/user      round 1, la saison démarre
    13:39:19  POST match   {"squadId":4,"type":"OFFLINE","seasonId":1,"divisionId":10,…}
    13:48:12  PUT  match/end                      QUIT, 18 formes, 1 but, 1 passe
    13:48:12  PUT  season/1/division/10/user      round 2

L'écran de fin affiche « MATCHS RESTANTS 9 », « BILAN 0-0-1 », la forme, et la
barre montée/maintien. C'est le même appel de création de match que les
coupes, avec `seasonId`/`divisionId` à la place de `tournamentId`.

## Correction du 13 août, après-midi : `divisionId` est un index *client*

Ce qui est écrit plus haut — « `divisionId` est le rang du disque dans la
liste servie » — est faux, et l'écran l'a dit d'un coup : avec `divisionId: 0`
l'écusson de la Saison Actuelle affiche **DIV 1**, avec à côté « Matchs
restants : 10 » et « 12 PTS TITRE ». Aucune de ces valeurs n'est dans le
disque servi pour la division 10.

Donc `divisionId` indexe **la table de divisions du client**, qui va de la
Division 1 (index 0) à la Division 10 (index 9). Dix gelait parce que dix est
un cran au-delà de son dernier index — ça, c'était juste. Zéro tenait l'écran
parce que zéro est un index valide, pas parce que c'était le bon : il mettait
le club en Division 1.

`divisionId` vaut donc `division - 1`, et `SEASON_DIVISIONS` est réordonnée en
croissant pour que le même nombre soit juste des deux côtés. Vérifié sur la
console : le client sauvegarde maintenant dans
`PUT /ut/game/fifa14/season/10/division/10/user`, et le panneau de détails
affiche les valeurs de la division 10 — titre 12, montée 2, 400 / 1 500 / 300.

Le client compte **dix rencontres** par saison quoi qu'on serve : après un
match abandonné en division 10, où cette table n'en donnait que quatre,
l'écran de fin annonçait « MATCHS RESTANTS 9 ». Les dix divisions en ont dix.

## Ce qui reste : reprendre une saison entamée

Une saison au round 2 est proposée à nouveau au démarrage — « Voulez-vous
vraiment débuter cette Saison Joueur Solo ? » — et la colonne Score de la
liste des rencontres reste vide.

Ce n'est pas faute d'avoir l'information. Le serveur la tient et la sert :

    season/user  ->  {"seasonId":10,"divisionId":9,"round":2,
                      "seasonGamesWon":0,"seasonGamesDraw":0,"seasonGamesLost":1,
                      "seasonCoins":0}

Et le blob que le client s'était sauvegardé lui est rendu intact sur
`season/10/division/10/user`. Ce qu'on sait de plus, et qui délimite la
recherche :

* **le client ne redemande pas le blob.** En rouvrant le mode il ne lit que
  `season/list` puis `season/user`, puis ne parle plus au serveur du tout —
  la liste des rencontres est dessinée hors ligne. La seule fois où il a
  demandé `season/<id>/division/<div>/user`, le 13 août à 14:41:38, la route
  n'était pas encore écrite et il a pris un 404.
* **`round` n'est pas lu non plus**, ou pas là. Round 2 servi, et la première
  rencontre reste marquée « SUIV. ».
* **les quatre membres de bilan sont ignorés.** `seasonGamesWon` et
  `seasonCoins` sont partis avec les bonnes valeurs à 15:32:11 et l'en-tête
  affichait `BILAN 0-0-0` et `CRÉDITS 0`.

Ce qui reste debout, par élimination : l'état de reprise est dans les disques
de `matches`, que la colonne Score attend. `score` est dans la table de noms
du module. Ce qui manque pour le servir, c'est le score encaissé : le corps de
`match/end` porte les buts de chaque joueur — donc les miens — et rien sur
l'adversaire. Le servir demanderait d'inventer la moitié de chaque score, ce
que ce dépôt ne fait pas.

## Rectification : l'écusson ne suit pas `divisionId`

J'ai écrit plus haut que le badge **DIV 1** prouvait que `divisionId` indexe la
table du client. Il ne prouve rien : vérifié le 13 août à 16:27, avec
`divisionId: 9` servi et la liste réordonnée, **l'écusson affiche toujours
DIV 1**. Il l'affichait déjà avec `divisionId: 0`. Il ne suit donc pas ce
membre, et sa valeur est constante quoi qu'on envoie — vraisemblablement un
défaut du client, ou l'asset de trophée, que `trophyResourceId: -1` laisse
sans rien.

Ce qui reste vrai de ce raccourci :

* **dix gèle**, zéro et neuf tiennent. La borne est réelle.
* le panneau de détails affiche bien les seuils et récompenses de la division
  visée — mais il le faisait dans les deux configurations, puisque la liste a
  été réordonnée en même temps. Il ne discrimine pas non plus.

Autrement dit, entre `divisionId = 0` avec liste décroissante et
`divisionId = 9` avec liste croissante, **rien d'observé ne les sépare**. Le
second est gardé parce qu'il donne au membre le même sens des deux côtés, pas
parce que la console l'a confirmé.

Ce qui trancherait, en une relance : servir `divisionId: 5` et lire l'écusson.
`6` désignerait `divisionId + 1`, `5` désignerait `10 - divisionId`, et DIV 1
à nouveau dirait que le badge ne vient pas de nous du tout.

## Le test `divisionId: 5`, et ce qu'il règle

Servi le 13 août à 16:52, avec `FIFA14_SEASON_DIVISION_ID=5` et rien d'autre
de changé :

    season/user  ->  {"seasonId":10,"divisionId":5,"round":1}

Deux lectures à l'écran, et elles éliminent toutes les deux ce membre.

**L'écusson affiche DIV 1.** Ni 6 ni 5 : la même valeur qu'avec 0 et qu'avec
9. Le blason de la Saison Actuelle ne suit pas `divisionId`, et il ne vient
pas de ce document. Il vaut d'ailleurs DIV 1 sur la tuile **Saison en ligne**
aussi, un mode que ce serveur ne sert pas du tout — c'est donc un défaut du
client.

**Les récompenses restent celles de la division 10.** Championnat 400, Montée
1 500, Maintien 300 — le disque de la division 10 — alors que l'index 5 de la
liste servie est la Division 6, qui vaudrait 1 000 / 600 / 300. Donc
`divisionId` ne choisit pas non plus le disque.

Ce qui laisse : **`divisionId` ne pilote rien d'observable**. Sa seule
propriété établie est qu'à 10 il gèle l'écran, ce qui reste une borne réelle
et la raison pour laquelle il faut lui donner une valeur basse.

Ce qui choisit le disque, alors, c'est le client lui-même : sa requête porte
`divisionList=10` depuis la toute première entrée dans le mode, le 12 août à
21:03, avant qu'aucune saison n'existe côté serveur. **La division est tenue
côté client**, et `season/user` ne la lui apprend pas.

Ça recadre la recherche sur la reprise : si la division vit chez le client, la
progression aussi, et ce qu'il faut lui rendre est ce qu'il a lui-même
sauvegardé — le blob de `season/<id>/division/<div>/user`, qu'il ne redemande
pourtant jamais à la réouverture. C'est là qu'il faut chercher, pas dans
`season/user`.

## Le round d'une saison se compte ici — 14 août 2026

`season/user` déduisait le round du blob que le client sauvegarde. Le client
réécrit ce blob en entrant dans le mode et y remet `round` 1 : le 14 août à
01:53, une saison avec un match déjà gagné est revenue à 1. Un round tiré de là
annonce « dix matchs restants » indéfiniment.

Le compte existait déjà de notre côté : `SeasonProgress.settle` enregistre
chaque résultat. `_season_matches_played` s'en sert, et ne retombe sur le blob
que pour une saison restaurée d'une sauvegarde antérieure au bilan.

C'est ce que fait le revival PC (`KyroGeorge2/FIFA-14-Local-FUT`,
`offline_season_user`) : une colonne `matches_played` à lui, et
`round = matches_played + 1`.

Deux choses relevées au passage dans ce projet, non appliquées :

- sa table de divisions est ordonnée **décroissante**, Division 10 en premier,
  et il sert `seasonId: 1` — l'index 0. La nôtre est croissante et sert
  `seasonId: 10`. Les deux désignent la Division 10 dans leur propre ordre,
  donc les deux sont cohérents ; c'est ce qui explique la clé `10:10` là où le
  13 août donnait `1:10`.
- il **omet délibérément** les membres de bilan (`Unknown guessed progression
  members are intentionally omitted`). On les envoie ; on a déjà observé qu'ils
  ne sont pas lus, donc c'est sans effet, mais ce n'est pas nous qui avons
  raison.

Non vérifié à l'écran : le round servi n'a jamais fait bouger la liste des
rencontres jusqu'ici.


## Le lecteur de saison, désassemblé — 14 août 2026

Rectification : j'avais écrit ici que la table ne connaissait pas `data` et que
le nom lu était `seasonData`. Faux. La table contient bien les deux (`data` à
133, `seasonData` à 443), et c'est **`data`** que le lecteur compare.

Le lecteur est `CardsDLLzf+0x1adf28` — le jumeau de celui des coupes. Même
répartition sur des identifiants numériques contre la table `0x8921E498` :

| id | membre | ce qu'il en fait |
|---|---|---|
| 133 | `data` | alloue, et remplit les registres tampon + longueur |
| 134 | `dataVersion` | entier ; **s'il vaut 1, décode** avec ces registres |
| 148 | `divisionId` | entier, demi-mot en 0x30 |
| 429 | `round` | entier **moins un**, demi-mot en 0x38 |
| 445 | `seasonId` | entier, mot en 0x34 |

Deux choses en tombent :

- **Le même piège d'ordre que pour les coupes.** Le sérialiseur de saison
  (0xa35c) écrit `{"round":…,"dataVersion":…,"data":"…"}`, donc `dataVersion`
  avant `data`, donc le décodage part sur des registres jamais écrits. La
  réponse envoie maintenant `data` **avant** `dataVersion`.
- **`round` est stocké moins un**, ce qui confirme la lecture qu'on avait :
  round 1 sur le fil est la première rencontre, et 0 deviendrait le sentinelle
  invalide du client.

`seasonId` et `divisionId` sont acceptés mais pas envoyés : le client les a
déjà dans le chemin de la requête, et rien de non vérifié ne part sur une route
dont chaque essai coûte une console gelée.

## Le blob dans `season/user` ne suffit pas non plus — 15 août 2026

Testé pour de vrai cette fois, ce qui n'avait jamais été le cas.

### Pourquoi les essais précédents ne prouvaient rien

Le journal du 13 août montre que le client **sauvegarde bien** sa progression :

```
14:20:08  PUT round=1  data=AAAAEAUAAAAB…   16 octets — saison neuve
14:38:57  match gagné
14:38:58  PUT round=2  data=AAACQB+LCAAA…   576 octets — la vraie progression
15:32:22  PUT round=1  data=AAAAEAUAAAAB…   retour à la saison vide
```

À chaque fois la saison avait ensuite été **redémarrée** — le « Oui » au modal —
et le client réécrivait son blob vide par-dessus. On lui rendait donc
fidèlement une saison sans progression. Le mécanisme était en place, les
données étaient vides.

### Le vrai test

Le 15 août à 00:49:48, forfait de match : le client a écrit `round=2` avec un
blob de 135 octets compressés, 576 annoncés. Le serveur l'a rendu tel quel dans
`season/user`, `data` avant `dataVersion` :

```
membres : seasonId, divisionId, round, data, dataVersion,
          seasonGamesWon, seasonGamesDraw, seasonGamesLost, seasonCoins
round = 2   blob = 135 octets (annoncé 576)
```

Le client ne l'applique pas. La liste des rencontres n'affiche aucun score, et
le modal « Voulez-vous vraiment débuter cette Saison Joueur Solo ? » revient.

### Ce que ça élimine

- **la progression manquante** — elle est là, réelle et compressée ;
- **l'ordre des membres** — `data` précède `dataVersion`, la règle qui a réglé
  les coupes ;
- **le round** — servi à 2, déjà éliminé le 13 août.

### Ce qui reste, et l'hypothèse à vérifier

L'hypothèse jamais prouvée est que `season/user` soit lu par
`CardsDLLzf+0x1adf28`, le lecteur à cinq membres. Elle tient debout — c'est le
seul lecteur qui connaît `data`, `dataVersion`, `divisionId`, `round` et
`seasonId` ensemble — mais rien ne la démontre.

La table des routes, à `0x89027088`, ne la tranche pas : ce ne sont que des
paires `{gabarit, nom}`, sans pointeur de handler. Les gabarits ne sont pas non
plus référencés par `lis`/`addi`, donc l'aiguillage se fait par nom ailleurs.

Le pas suivant qui ne serait pas une devinette : `0x891adf28` écrit son état
dans une structure — `round` en `+0x38`, `divisionId` en `+0x30`, `seasonId` en
`+0x34`, le blob décodé en `+0x20`. Retrouver cette structure en mémoire pendant
que l'écran est ouvert, et lire son `round`, dit en une mesure si notre document
a été appliqué ou ignoré. Contrairement aux coupes il n'y a pas de gel à
attraper, donc il faut chercher la structure plutôt que le thread.

## Notre document est lu, décodé, et rangé — le rendu l'ignore — 15 août 2026

Suite du désassemblage, sans console.

Le conteneur de réponses de CardsDLL tient 72 objets, un par famille de route.
**Un seul** lit la forme d'une saison : `CardsDLLzf+0x1adf28`, à `conteneur+0x568`,
qui compare exactement `data`, `dataVersion`, `divisionId`, `round`, `seasonId`.
Deux objets coupe (`+0x448`, `+0x508`) ont les mêmes membres blob.

La branche `data` (id 133, 0x891ae0e8) **recopie** le blob dans un tampon alloué
par `0x891add68`, et la branche `dataVersion` (id 134) l'**inflate** via le
décodeur `0x891b3dd0` — les deux mêmes fonctions que le décodeur de coupe. Donc
`season/user` est parsé intégralement et son blob est décodé dans l'objet
saison. On envoie la bonne chose, dans le bon ordre, et le client la lit.

Le défaut est donc **en aval du parsing**, et c'est ce qui distingue la saison
de la coupe :

- pour une coupe, peupler l'objet suffisait : l'écran du tableau lit dedans ;
- pour une saison, l'écran « LISTE DES RENCONTRES » tire ses scores d'une autre
  source que cet objet. Les équipes viennent de `season/list` et s'affichent ;
  la colonne Score vient de l'état de saison et reste vide.

Ça explique pourquoi ni round 2 ni le vrai blob n'ont rien changé : le parsing
marchait déjà, le rendu ne consulte pas l'objet peuplé.

Deux impasses de méthode, notées pour ne pas les refaire :

- **Lecture passive en mémoire** : l'objet saison porte la vtable `0x89029e18`
  en tête et `round` en `+0x38`, mais le conteneur (`0x8919b020`, vtable
  `0x890258b8`) est tenu dynamiquement — aucune globale de CardsDLL ne pointe
  dessus (1 903 candidats testés). La chaîne de pointeurs statique ne se résout
  pas jusqu'à une racine.
- **XBDM** n'offre pas de recherche mémoire, et lit à 53 Ko/s quelle que soit la
  taille de bloc — 200 Mo de tas de jeu = plus d'une heure, inexploitable pour
  un scan aveugle.

Prochain pas qui n'est pas une devinette : trouver **qui lit en retour** le
champ blob de l'objet `+0x568` (le tampon rempli par la branche `data`). Si rien
dans le chemin de rendu ne le lit, l'état de saison est purement côté client et
aucune réponse serveur ne peut le restaurer — ce serait la vraie réponse, et
elle clôt la question au lieu de l'ouvrir encore.

## La cause, cernée : le client ne va jamais chercher son blob de saison — 15 août 2026

Comparaison avec la coupe, qui se reprend, entièrement hors console.

Les deux objets sont **structurellement identiques**. Le lecteur de coupe
(`0x891be840`) et celui de saison (`0x891adf88`) décodent tous deux leur blob à
`objet+0x20`, avec le même décodeur `0x891b3dd0`. Même disposition de champs
(`round` en `+0x38`, `divisionId` en `+0x30`), même code. Notre document de
saison est donc désérialisé et décodé exactement comme celui d'une coupe.

La différence est **sur le fil**, dans la façon dont le blob arrive :

| | coupe (marche) | saison (ne marche pas) |
|---|---|---|
| liste des reprenables | `GET tournament/user/list` | `GET season/user` |
| récupération du blob | **`GET tournament/user/<id>`** | *jamais demandé* |

Pour la coupe, `tournament/user/list` énumère les ids reprenables, le client
fait un GET par tournoi, le blob revient, l'écran du tableau le consomme. Pour
la saison, ce GET par saison **n'a jamais lieu** sur aucun journal — le client
ne va jamais chercher son blob.

Conséquence, et elle clôt une longue fausse piste : le **contenu, l'encodage et
l'ordre** du blob de saison sont corrects. Le problème n'a jamais été là.
Servir `data` avant `dataVersion` était juste — les deux lecteurs l'exigent —
mais insuffisant, parce que le blob correct n'est jamais réclamé.

Ce qui reste à trancher est côté client (ion_fut/Lua, absent de ce dump) :
qu'est-ce qui, dans la réponse à `season/user`, ferait croire au client qu'une
saison est en cours et déclencherait la récupération de son blob ? Pour la
coupe, c'est la liste. Pour la saison, il n'y a pas d'équivalent identifié. La
prochaine expérience — ciblée, plus une devinette — est sur le fil : faire
signaler à `season/user` une saison en cours et voir si le client émet enfin un
GET vers `season/<id>/division/<div>/user`. Elle coûte un aller-console et
risque un gel (les membres de `season/user` sont connus fragiles), mais elle
teste une hypothèse précise.

## Conclusion : la reprise de saison n'est pas atteignable depuis le serveur

L'expérience « signaler une saison en cours » a été résolue hors console, parce
que le levier qu'elle suppose n'existe pas. Quatre angles, tous concordants :

1. **Lecteur de `season/user`** (`0x891adf88`) : cinq membres — `data`,
   `dataVersion`, `divisionId`, `round`, `seasonId`. Aucun drapeau d'état. Un
   membre « en cours » y serait muet.
2. **Lecteur de `season/list`** (`0x891c3510`) : que des définitions statiques
   (prix, matches, dates, trophée). Aucun membre de progression par joueur.
3. **Table des routes** (`0x89027068`) : il n'y a **pas** de `season/user/list`.
   Les coupes en ont une (`tournament/user/list`) qui énumère les reprenables
   et pilote le GET du blob par tournoi. Les saisons n'ont pas d'équivalent.
4. **Historique du fil** : 13 requêtes vers `season/<id>/division/<div>/user`,
   **toutes en PUT, zéro GET**, sur tous les journaux. Le client n'a jamais été
   chercher son blob de saison.

Ce que la coupe a et que la saison n'a pas, ce n'est ni la forme du blob ni son
encodage — les deux objets sont le même code, décodant au même offset. C'est le
**mécanisme d'énumération** : `tournament/user/list` → GET par id → l'écran
consomme. Sans lui, le client ne réclame jamais la saison sauvegardée, et aucune
réponse serveur ne peut l'y forcer.

Donc, pour ce client : la saison se joue match par match dans une session, mais
**ne se reprend pas après une sortie** de l'écran. Ce n'est pas un défaut du
serveur, c'est une limite du client — il ne va pas rechercher l'état qu'il a
lui-même sauvegardé. La reprise resterait possible avec un patch **côté client**
(faire émettre le GET manquant), ce qui est un autre chantier que celui-ci.

Ce qui reste correct et acquis : `season/user` sert bien `data` avant
`dataVersion`, l'en-tête (bilan, crédits, round) est exact, et
`SeasonProgress.current()` choisit la bonne entrée. Rien de tout cela n'est à
défaire — c'est simplement insuffisant pour contourner une limite qui n'est pas
de notre côté.

## Ce qui manquait n'était pas le blob — 4 septembre 2026

La conclusion ci-dessus — « la reprise de saison n'est pas atteignable depuis
le serveur » — repose sur un fait exact et sur une déduction fausse.

Le fait tient : sur tous les journaux, treize requêtes vers
`season/<id>/division/<div>/user`, **toutes en PUT, zéro GET**. Le client ne va
jamais rechercher le blob qu'il a lui-même sauvegardé, et les coupes ont un
`tournament/user/list` que les saisons n'ont pas.

La déduction — donc aucune réponse serveur ne peut restaurer une saison — est
démentie par un serveur qui le fait. AC, qui a rebâti FIFA 14 sur PC, décrit
son montage le 31 août 2026 : il ne compte sur aucun GET séparé, **il renvoie
le blob dans `season/user`**, exactement là où ce dépôt le renvoyait déjà.

Ce qui lui manquait était ailleurs, et il le dit en une phrase :

> The match/end reply then carries the announcement the client acts on:
> `seasonEndResult` + `seasonCoins` — without those the client keeps offering
> fixtures.

### Ce que le serveur savait et ne disait pas

`SeasonProgress.settle` enregistre chaque résultat depuis le 16 août. Son
retour n'allait **qu'au journal**, pour écrire la ligne `BILAN` ; rien de ce
qu'il calculait n'entrait dans la réponse à `match/end`. Et il ne calculait pas
tout : ni les points (3/1/0), ni le franchissement d'un seuil, ni la clôture.
`divisionOffline` valait `10` en dur, donc une montée n'avait nulle part où
s'inscrire — un club pouvait gagner sa saison et rouvrir le mode en Division 10.

`seasonEndResult` n'est pas un nom deviné : la table de noms de CardsDLL le
porte à `0x101c0`, relevée dans `docs/TOURNAMENTS.md`.

### L'identité d'une saison, qui n'est plus une position

`seasonId` a porté trois lectures ici — la position dans la liste servie, la
position dans la table des dix, le numéro de division — et les pages qui
précèdent racontent une journée entière passée à les départager sans y arriver.
La raison est mécanique : **sur une liste de dix, la position d'une division et
son numéro sont le même nombre.** Une liste réduite les sépare enfin, et donne
alors deux réponses opposées selon la ligne qu'on lit.

AC ne tranche pas ce débat, il le supprime. Une saison porte un identifiant qui
n'est la position de rien : la Division 1 est `id` 1103, `season/user` répond
`seasonId` 1103, et `trophyResourceId` vaut 1103 aussi. `season_id(division)`
place la série à 1102 + numéro, soit 1103..1112, ce qui tombe dans les soixante-
dix trophées que `cards0.big` embarque à 1100..1169 — c'est pourquoi le même
nombre peut servir d'identifiant et de trophée.

Tous les modes partagent désormais cette identité, y compris `kyro`, qui cesse
donc d'être une reproduction fidèle du build de référence sur ce point. C'est
assumé : un mode dont le `seasonId` ne désigne aucune ligne de sa propre liste
est exactement le défaut qu'on retire.

### `dataVersion` part en chaîne

Le lecteur `CardsDLLzf+0x1adf28` traite le membre 134 comme un entier et ne
décode le blob que **s'il vaut 1**, avec les registres que le membre 133 a
remplis. Ce dépôt envoyait l'entier `1` ; le build qui reprend ses saisons
envoie la chaîne `"1"`. C'est la seule différence jamais observée sur cette
branche, et elle coûte un caractère.

Non vérifié sur console. C'est une hypothèse, servie parce qu'elle est gratuite
et qu'elle vient d'un build qui marche — pas parce qu'on l'a mesurée.

### Ce que `season/list` sert maintenant

Trois niveaux de récompense au lieu de quatre, du seuil le plus haut au plus
bas : `CHAMPIONSHIP`, `PROMOTION`, `MAINTENANCE`. La relégation n'est pas un
niveau de récompense — elle ne paie rien, et son seuil valait zéro, le même que
le maintien, donc les deux premières entrées étaient indiscernables.
`season_outcome` lit cette liste dans l'ordre et retient le premier seuil
atteint, ce qui garantit que le règlement et l'écran de détails ne peuvent pas
diverger.

Avec : `trophyUseCount` 1, `visStartDays` 0 et `visEndDays` 365, des dates
réelles au lieu de 0 et 0x7FFFFFFF, et `untilEndSeconds` à un an plutôt qu'à
dix. `matchLengthMin` reste à 6 : AC sert 1, mais c'est une durée de match, pas
une pièce du protocole, et 6 est ce que FUT joue.

La liste est ordonnée **Division 10 d'abord**. L'ordre ne désigne plus rien
depuis `season_id` ; il ne fait qu'une chose, choisir la tuile sur laquelle
l'écran s'ouvre, et un club commence en Division 10.

### Ce qui reste à vérifier sur la console

Tout. Rien de cette page n'a encore été mesuré sur le matériel : ce sont des
documents rendus cohérents avec un serveur qui fonctionne ailleurs. Les trois
choses à lire, dans l'ordre :

1. l'écran s'ouvre-t-il ? `FIFA14_SEASON_MODE=native` reprend l'ancien défaut
   si `ac` gèle ;
2. l'écusson et le panneau de détails montrent-ils la bonne division ;
3. après dix rencontres, l'annonce de fin tombe-t-elle, et la division
   suivante suit-elle ? Le journal porte `seasonEndResult`, `seasonPrize`,
   `seasonPoints` et `seasonDivision` sur l'événement `fut_match_end`, donc la
   réponse se lit sans écran.

### Mesuré sur la console — 4 septembre 2026, 19:39

L'écran s'ouvre, et **la saison se reprend**. C'est ce que la section
« Conclusion » ci-dessus déclarait hors d'atteinte depuis le serveur.

Ce que la console a demandé en entrant dans le mode :

    17:17:28  GET season/user                     type=offline
    17:19:18  GET season/list                     divisionList=10
    17:19:18  GET /fut/items/xbl2/1112 … 1103     dix, une par division
    17:19:18  GET season/user
    17:19:18  GET trophies/xbl2/item.big
    17:19:20  GET season/user/history             ← le 13 août, plus rien ici

Les dix requêtes d'items portent exactement `1103`..`1112`, donc le client lit
le nouveau `trophyResourceId` et distingue les dix divisions. Le panneau de
détails de la Division 10 affiche **12 pts titre, 2 montée, 1900 / 1500 / 300**
— les trois montants sont ceux du disque servi, là où le 13 août relevait 400
en championnat.

Puis une rencontre gagnée :

    17:38:44  fut_match_end     WIN, seasonRecord 1-0-1, season [1112, 10]
    17:38:44  PUT season/1112/division/10/user    round 3, blob 704 o
    17:39:03  GET season/list, season/user, season/user/history

Et à la ré-entrée, l'écran affiche **deux matchs joués et huit restants**. Pas
de modal « Voulez-vous vraiment débuter cette Saison Joueur Solo ? ».

L'état côté serveur, lu dans la sauvegarde du club :

    1112:10   round 3, gagné 1, perdu 1, 781 crédits, blob 352 o
    1:10      round 1, gagné 1        résidus de l'ancien schéma d'identité
    10:10     round 1, vide
    division  offline 10

`SeasonProgress.current()` retient `1112:10` parce que c'est l'entrée avec le
plus de rencontres derrière elle — la règle écrite en août pour exactement ce
cas tient face aux deux résidus.

**Ce qui reste non mesuré :** l'annonce de fin de saison. Elle ne tombe qu'à la
dixième rencontre, et deux ont été jouées. Le journal la portera sur
`fut_match_end` — `seasonEndResult`, `seasonPrize`, `seasonPoints`,
`seasonDivision` — donc elle se lira sans avoir à croire l'écran.

**Ce qui n'est pas départagé :** `dataVersion` en chaîne. Le client envoie
lui-même l'entier `1` dans son PUT ; on lui répond `"1"`, et ça reprend. Donc
la chaîne n'empêche rien — mais rien ne dit que l'entier aurait échoué. Ce
changement voyageait avec quatre autres et aucun essai ne l'isole. Le
départager coûterait une relance et une saison à rejouer, pour une question
qui n'a plus de conséquence.

**Un défaut cosmétique repéré au passage :** sur les dix trophées demandés,
seuls `1104`, `1108` et `1112` résolvent vers une définition réelle (154-161
octets) ; les sept autres reviennent à 112, la réponse vide. Sept divisions
auront donc un écusson nu. C'est de l'illustration, pas du protocole.
