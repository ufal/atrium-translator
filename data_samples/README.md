### MTX201501307.alto.xml anonymized to be used as a translator test input:

Only `String CONTENT="..."` values were touched. All XML structure, namespaces, schema reference, ParagraphStyle IDs, 
block/line/glyph geometry (HPOS/VPOS/HEIGHT/WIDTH), coordinates, and the survey-point dump at the end are byte-identical. 
The BOM and CRLF line endings are preserved exactly, and the file still validates as ALTO v3 XML and has the same **5527** lines.

Grammar preserved via inflection-aware mapping. Czech declines names heavily, so each inflected surface form maps to 
a matching invented form in the same case/paradigm — e.g. `Nového Města` → `Starého Sídla`, `Nedvědička`/`Nedvědičkou` 
→ `Vrbice`/`Vrbicí`, `Heralt`/`Heraltovi`/`Heraltově` → `Načerat`/`Načeratovi`/`Načeratově`. Punctuation attached to 
tokens (commas, periods, citation parens, quotes) is stripped, matched, and reattached, so bibliography entries and 
parenthetical citations keep their shape.

#### Entities replaced (95 distinct surface forms):

- Village `Zubří` → `Vraní`, with the etymology line kept coherent (`"místo kde jsou zubři"` → `"místo kde jsou vrány"`, 
both matching the new crow-derived name).

- Places/hydronyms: `Nové Město na Moravě`, `Praha-Chodov`, `Žďár nad Sázavou`, `Jihlava`, `Brno`, 
`Bítešská vrchovina`, `Harusův kopec`, `Nedvědička`/`Divišovský potok`, `Olešná`, `Lažínek`, `Jevišovka`, 
`Střelice`, `Loučka`, `Olešínky`, `Pohledec`, `Jimramov`, plus historical estates (`Bystřice`, `Pyšolec`, 
`Kunštát`, `Pernštejn`, `Ditrichštejn`, `Boskovice`, etc.).

- Team members (`Baier`, `Kaiser`, `Bařinka`, `Švácha`, `Hrušková`, `Hoffmannová`, `Kossl`) and historical figures 
(`Jimram`, `Jošt`, `Heralt`, the `Lucemburks`, etc.) all given invented equivalents.

- Deliberately left generic words that merely resemble entities (`moravský`, `bystřické`, `plynové`, `pánové`, etc.) 
untouched. 

- The company name `Pueblo – archeologická společnost` was left as-is since it's the 
[report producer](https://www.pueblo-archaeology.org/home), not a personal or place name.
