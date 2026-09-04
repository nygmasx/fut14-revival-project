# World Cup Ultimate Team : le code est là, les données ne le sont pas

Enquête du 4 septembre 2026, console 1 en main. La question posée était : le
mode est-il disponible sur le title update qu'on utilise, ou faut-il en
installer un autre ?

**Réponse courte.** TU3 *est* la mise à jour Coupe du Monde. Il n'y a pas de
TU plus récent à aller chercher. Le mode est entièrement dans le binaire, il
est fermé par un drapeau que ce serveur contrôle — et les **mises en page de
son interface ne sont pas dans les archives installées**.

## Ce que la console fait tourner

    xbeinfo running   \Device\Harddisk0\Partition1\Games\FIFA 14\default.xex
    title id          0x454109C3
    media id          15467F11
    version           0.0.0.3        base 0.0.0.3
    default.xex       construit le 2014-04-15 01:20:55 UTC
    CardsDLLzf        construit le 2014-04-15 02:08:50 UTC
    powdllzf          construit le 2014-04-15 02:14:41 UTC

Le 15 avril 2014 est la date de la mise à jour Coupe du Monde de FIFA 14. La
version du XEX est **3**, et sa version de base est 3 aussi : l'installation
est un rip dont l'exécutable a déjà été fusionné avec TU3.

Aucun autre title update n'est installé — le seul `TU_` de `Hdd:\Cache` est
`TU_134U200_...`, et son en-tête STFS dit **PAYDAY 2**, title id 0x464F0800.

## Ce que le binaire connaît

Lu dans le `.rdata` de CardsDLL, aux offsets indiqués :

| offset | chaîne |
|---|---|
| 0x0156b7 | `0data\ui\layout\fut\FutFluxHubWCCfg.xml` |
| 0x0156e8 | `external.ion_fut.components.Tile.Addon_AssignKitWC` |
| 0x01572c | `GOTO_WORLD_CUP` — dans la liste des destinations du hub, entre `UPDATE_PANELS` et `GOTO_CHANGE_CLUBNAME` |
| 0x028c54 | `enableWorldCupMode` — dernier membre de la liste que le parser de `/settings` compare |
| 0x02b17c | `/schedule/tournamentid/%u` |
| 0x02b76c | `/teams?groupId=%d&count=%d` |
| 0x02dcbc | `/user/tournament`, suivi à 0x02dcd0 de `{"cupsWon":%d}` |
| 0x02ee68 | `/season/user/history?type=WC_TOURNAMENT_OFFINE` |
| 0x02ee98 | `/season/user/history?type=WC_TOURNAMENT_ONLINE` |
| 0x02eec8 | `wc_tournament_offine` |

`OFFINE` est la faute de frappe d'EA, pas la nôtre. Elle est dans le binaire et
il faut la servir telle quelle.

Cinq destinations de flux : `GOTO_WC_TOURNAMENT_HISTORY`, `_PLAY_MATCH`,
`_STAGE`, `_STATS`, `_TREE`. Une deuxième mise en page,
`FutFluxOfflineTournamentWCCfg.xml`. De l'art : `TournamentWCHub_%s.dds`,
`FIWC_Ball`. Des attributs de carte : `CONFEDERATIONWC_ID`, `NATIONALITYWC_ID`.

Et neuf fonctions natives, qui décrivent la forme du mode mieux que tout le
reste — une phase de groupes puis un arbre :

    GetOfflineWCTournamentGroupData      GetOnlineWCTournamentGroupData
    GetOfflineWCTournamentGroupSchedule  GetOnlineWCTournamentGroupSchedule
    GetUserCurrentWCRound                GetUserWCGroupMatchHistory
    UpdateWCGroupData                    SaveWCTournyGroupLastMatchData
    CardsSaveTournamentWC

## Ce que les archives ne contiennent pas

Le sommaire d'une archive `BIG4` est en tête de fichier, donc il se lit sans
télécharger l'archive entière. Relevé sur les six accessibles :

| archive | fichiers | `data/ui/layout/fut` | Coupe du Monde |
|---|---|---|---|
| `data1.big` | 17 449 | **6** | 0 |
| `cards0.big` | 2 433 | 0 | 0 |
| `data2.big` | 209 | 0 | 0 |
| `data5.big` | 83 | 0 | 0 |
| `data7.big` | 1 341 | 0 | 0 |
| `datax.big` | 118 | 0 | 0 |

`data1.big` est l'archive d'interface, et les six mises en page qu'elle porte
sont :

    futfluxauctionsearchcfg.xml   futfluxhubcfg.xml
    futfluxofflineseasonscfg.xml  futfluxonlineseasonscfg.xml
    futfluxpausecfg.xml           futfluxpauseendcfg.xml

`futfluxhubwccfg.xml` et `futfluxofflinetournamentwccfg.xml` **n'y sont pas**,
et aucune autre archive ne porte de `data/ui/layout/`.

`data3.big` et `data6.big` n'ont pas pu être lues : elles font 1,35 et 1,37 Go
et `tools/xbdm_getfile.py` refuse au-delà d'un gigaoctet. Ce sont, d'après leurs
voisines, des archives de médias — `data2`, `data5` et `data7` ne contiennent
que de l'audio, des stades et des textures. L'arbre `data/ui` vit entièrement
dans `data1`. C'est une présomption, pas une preuve, et c'est la seule case
non cochée de ce tableau.

Le dossier `dlc/` ne contient que trois DLL — `CardsDLLzf.xex.dll`, `powdll`,
`FootballCompEng` — et pas un octet d'interface. Son `info.dlc` s'annonce
d'ailleurs encore comme « FIFA 10 Ultimate Team (English) ».

## Ce que ce serveur sert maintenant

`enableWorldCupMode` n'est plus un zéro écrit en dur : `FIFA14_WORLD_CUP=1`
l'ouvre. **Désarmé par défaut**, et le rester tant que la question des mises en
page n'est pas tranchée — un hub FUT qui gèle coûte une relance du titre, et ce
dépôt l'a payé deux fois sur les saisons.

Les deux routes que le mode ajoute et qui tombaient sur le 404 générique
répondent désormais du vide bien formé : `/tournament/schedule/tournamentid/<n>`
et `/user/tournament`, cette dernière avec `{"cupsWon": <trophées>}`, la forme
que le binaire porte à côté de la route. Les quatre orthographes de
`season/user/history` — les deux ordinaires et les deux Coupe du Monde —
passaient déjà par `season_history_response`.

Un 404 sur une route FUT ne dit rien : il laisse un écran vide ou gelé, sans
message. C'est ce que `season/user/history` a coûté avant d'être écrit.

## Ce qu'il reste à faire, et dans quel ordre

**1. Allumer et regarder.** `FIFA14_WORLD_CUP=1`, entrer dans FUT, et lire le
journal. Trois issues, toutes informatives : une tuile Coupe du Monde apparaît
et s'ouvre — alors les mises en page sont ailleurs et ce document se trompe ;
une tuile apparaît et gèle — les mises en page manquent bien, et on sait quoi
aller chercher ; rien n'apparaît — le drapeau ne suffit pas seul.

À faire avec quelqu'un devant la console, pas en aveugle.

**2. Si les mises en page manquent : récupérer le vrai conteneur TU3.**
L'installation est un rip à XEX fusionné. Un TU officiel est un conteneur STFS
qui peut porter des données en plus de l'exécutable, et ce rip n'en aurait gardé
que l'exécutable.

Le chemin propre existe et ne demande aucun téléchargement douteux : **le disque
FIFA 14 est là** — la console 2 fait justement tourner le jeu depuis le DVD. Un
disque s'annonce en version de base 0 ou 1, donc la console, qui est sur Xbox
Live, proposera d'elle-même le title update et le déposera dans `Hdd:\Cache`.
Il n'y aura plus qu'à le tirer par XBDM et lire son sommaire.

**3. Ce que le mode demandera ensuite** se lit dans les neuf fonctions natives :
des données de groupe et un calendrier, hors ligne et en ligne, le round courant
de l'utilisateur, et l'historique de ses matchs de groupe. Rien de tout ça n'a
jamais été demandé sur aucun journal de ce projet — il n'y a donc rien à
deviner tant que le mode n'a pas parlé.

Les 1 077 cartes Coupe du Monde sont déjà là, sorties du catalogue par
`tools/worldcup_cards.py split` et rangées dans
`server/fifa14_cards_worldcup.json`. `restore` les remet.
