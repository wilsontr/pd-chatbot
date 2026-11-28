# Corpus sources stored in the repository

## `pd_file_format.json`

*Unofficial PD v0.37 fileformat specification*, by Simon Asselbergs and Tjeerd
Sietsma (Hogeschool voor de Kunsten Utrecht, 2004), as published at
<https://puredata.info/docs/developer/PdFileFormat>.

> Copyright (c) 2005 Free Software Foundation. Permission is granted to copy,
> distribute and/or modify this document under the terms of the GNU Free
> Documentation License, Version 1.2 or any later version published by the Free
> Software Foundation; with no Invariant Sections, no Front-Cover Texts, and no
> Back-Cover Texts. A copy of the license is online at
> <https://www.gnu.org/licenses/old-licenses/fdl-1.2.html>.

Modified for this project (TASK-5.41): the wiki's link markers and navigation
were removed, and the per-widget GUI records (bng, tgl, nbx, sliders, radios,
vu, cnv) and the EBNF appendix were left out. The remaining text is unchanged,
including its original spelling. The modified version is distributed under the
same licence.

It is the only description of the `.pd` format in the corpus. The Pd manual and
the object reference do not document it, so answers about `#X connect` or
`#X restore` lines had nothing to cite. It is unofficial and dates from Pd 0.37,
and every chunk and excerpt label says so.

`python parse_file_format.py` merges it into `parent_chunks.json` and
`child_chunks.json` (idempotently), and `chunk.py` includes it in a full
rebuild.
