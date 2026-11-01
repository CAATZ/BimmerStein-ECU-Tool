; monitor_flash_amd.asm — flash monitor for AMD/JEDEC 29F200/29F400 (P/R/E/F),
; stack-free (no calli/push), loaded at 0xFA60 in place of monitor_flash.asm (the 28F200
; monitor).  MAIN / MPING / MREAD and the wire protocol are IDENTICAL to monitor_flash.asm;
; only E (erase) and F (program) use the AMD command set, and there is NO VPP step:
;   'P'                                   -> 0xA5 (ping)
;   'R' pgLo pgHi offLo offHi lenLo lenHi -> set DPP0=page; reply <len> bytes (read-back)
;   'E' pgLo pgHi offLo offHi             -> AMD sector-erase at [off]; reply 0x80 ok / 0x20 fail
;   'F' pgLo pgHi offLo offHi lenLo lenHi <data> -> AMD word-program; reply 'K'  (len+off EVEN)
;
; AMD command set (vs Intel's single-cycle):
;   erase   : AA@555 / 55@2AA / 80@555 / AA@555 / 55@2AA / 30@sector
;   program : AA@555 / 55@2AA / A0@555 / data@addr
;   poll    : DQ6 TOGGLE bit (read twice; bit6 differs = busy) + DQ5. Erase rechecks
;             DQ6 after DQ5 to distinguish completion from a genuine device error.
; The flash is x16 on the byte bus (flash A0 = CPU A1) so the WORD command addrs 0x555/0x2AA are
; BYTE offsets 0xAAA/0x554.  The GAL only inverts A14 (above A10) so the command decode is
; unaffected (same as the alias).  Command/data WRITES are WORD (mov [Rw],Rw) — byte writes don't
; strobe WE# on this board; status READS are byte (movb) — DQ6/DQ5 are in the low byte.
;
; NO P2.6 / NO 12V: a 29F200/29F400 is single-supply (makes its own program voltage), and on this
; retrofit the 12V net also feeds the chip's RESET# (out of spec).  WR# (P3.13) is enabled at entry
; like the 28F200 monitor.  Reg use in E/F: r2=2nd-unlock-addr (after DPP0), r3=1st-unlock-addr,
; r4=target off, r5=cmd-data / poll read2, r6=count(F)/guard(E), r7=data word(F)/reply(E), r0/r1=scratch.
base 0xFA60
  bset DP3.13
  bset P3.13
MAIN:
MGET:
  jnb S0RIC.7,MGET
  movb RL0,S0RBUF
  bclr S0RIC.7
  cmpb RL0,#0x50
  jmpa cc_EQ,MPING
  cmpb RL0,#0x52
  jmpa cc_EQ,MREAD
  cmpb RL0,#0x45
  jmpa cc_EQ,MERASE
  cmpb RL0,#0x46
  jmpa cc_EQ,MFPROG
  jmpa cc_UC,MAIN
MPING:
  mov S0TBUF,#0xA5
MPA:
  jnb S0TIC.7,MPA
  bclr S0TIC.7
  jmpa cc_UC,MAIN
MREAD:
RP0:
  jnb S0RIC.7,RP0
  movb RL2,S0RBUF
  bclr S0RIC.7
RP1:
  jnb S0RIC.7,RP1
  movb RH2,S0RBUF
  bclr S0RIC.7
RA0:
  jnb S0RIC.7,RA0
  movb RL4,S0RBUF
  bclr S0RIC.7
RA1:
  jnb S0RIC.7,RA1
  movb RH4,S0RBUF
  bclr S0RIC.7
RC0:
  jnb S0RIC.7,RC0
  movb RL6,S0RBUF
  bclr S0RIC.7
RC1:
  jnb S0RIC.7,RC1
  movb RH6,S0RBUF
  bclr S0RIC.7
  mov DPP0,r2
MRLP:
  cmp r6,#0
  jmpa cc_EQ,MAIN
  movb RL7,[r4]
  bclr S0TIC.7
  mov S0TBUF,r7
MRTX:
  jnb S0TIC.7,MRTX
  add r4,#1
  sub r6,#1
  jmpr cc_UC,MRLP
MERASE:
EE0:
  jnb S0RIC.7,EE0
  movb RL2,S0RBUF
  bclr S0RIC.7
EE1:
  jnb S0RIC.7,EE1
  movb RH2,S0RBUF
  bclr S0RIC.7
EE2:
  jnb S0RIC.7,EE2
  movb RL4,S0RBUF
  bclr S0RIC.7
EE3:
  jnb S0RIC.7,EE3
  movb RH4,S0RBUF
  bclr S0RIC.7
  mov DPP0,r2
  mov r2,#0x0554       ; 2nd unlock byte-addr (word 0x2AA); r2 free after DPP0 set
  mov r3,#0x0AAA       ; 1st unlock byte-addr (word 0x555)
  mov r5,#0x00AA
  mov [r3],r5          ; AA -> 0x555
  mov r5,#0x0055
  mov [r2],r5          ; 55 -> 0x2AA
  mov r5,#0x0080
  mov [r3],r5          ; 80 -> 0x555  (erase setup)
  mov r5,#0x00AA
  mov [r3],r5          ; AA -> 0x555
  mov r5,#0x0055
  mov [r2],r5          ; 55 -> 0x2AA
  mov r5,#0x0030
  mov [r4],r5          ; 30 -> sector [off]  (erase confirm)
  mov r0,#0x2000       ; startup delay so the erase has begun (> the ~50us sector window)
EDLY:
  sub r0,#1
  jmpr cc_NE,EDLY
  mov r6,#0x0080       ; outer iteration guard (DQ5 fails fast; this just backstops a dead device)
EVO:
  mov r1,#0xFFFF       ; inner guard
EVI:
  movb RL0,[r4]        ; DQ6 read1 (low byte)
  movb RL5,[r4]        ; DQ6 read2
  xorb RL0,RL5         ; RL0 = read1 ^ read2  (RL5 still = read2)
  andb RL0,#0x40       ; isolate DQ6
  jmpr cc_EQ,EOK       ; DQ6 stable -> erase complete
  andb RL5,#0x20       ; DQ5 of read2 (exceeded timing limit)
  jmpr cc_EQ,EWAIT
  ; Read2 may already be erased array data (FF). Confirm DQ6 still toggles.
  movb RL0,[r4]
  movb RL5,[r4]
  xorb RL0,RL5
  andb RL0,#0x40
  jmpr cc_EQ,EOK
  jmpr cc_UC,EFAIL     ; still toggling after DQ5 -> genuine erase error
EWAIT:
  sub r1,#1
  jmpr cc_NE,EVI
  sub r6,#1
  jmpr cc_NE,EVO
EFAIL:
  mov r7,#0x0020       ; timed out / DQ5 -> no 0x80 + erase-error bit (host _decode_sr -> fail)
  jmpr cc_UC,ESEND
EOK:
  mov r7,#0x0080       ; synthesize Intel-style "ready" SR for the host's existing check
ESEND:
  bclr S0TIC.7
  mov S0TBUF,r7
ETX:
  jnb S0TIC.7,ETX
  jmpa cc_UC,MAIN
MFPROG:
FA0:
  jnb S0RIC.7,FA0
  movb RL2,S0RBUF
  bclr S0RIC.7
FA1:
  jnb S0RIC.7,FA1
  movb RH2,S0RBUF
  bclr S0RIC.7
FA2:
  jnb S0RIC.7,FA2
  movb RL4,S0RBUF
  bclr S0RIC.7
FA3:
  jnb S0RIC.7,FA3
  movb RH4,S0RBUF
  bclr S0RIC.7
FA4:
  jnb S0RIC.7,FA4
  movb RL6,S0RBUF
  bclr S0RIC.7
FA5:
  jnb S0RIC.7,FA5
  movb RH6,S0RBUF
  bclr S0RIC.7
  mov DPP0,r2
  mov r2,#0x0554       ; unlock addrs set ONCE before the loop (r2/r3 preserved across it)
  mov r3,#0x0AAA
MFL:
  cmp r6,#0
  jmpa cc_EQ,MFEND
FD0:
  jnb S0RIC.7,FD0
  movb RL7,S0RBUF
  bclr S0RIC.7
FD1:
  jnb S0RIC.7,FD1
  movb RH7,S0RBUF
  bclr S0RIC.7
  mov r5,#0x00AA
  mov [r3],r5          ; AA -> 0x555
  mov r5,#0x0055
  mov [r2],r5          ; 55 -> 0x2AA
  mov r5,#0x00A0
  mov [r3],r5          ; A0 -> 0x555  (program setup)
  mov [r4],r7          ; data word -> target [off]
  mov r1,#0xFFFF       ; per-word guard
FPI:
  movb RL0,[r4]        ; DQ6 read1
  movb RL5,[r4]        ; DQ6 read2
  xorb RL0,RL5
  andb RL0,#0x40
  jmpr cc_EQ,FPOK      ; done
  andb RL5,#0x20       ; DQ5?
  jmpr cc_NE,FPOK      ; error -> move on; host read-back verify catches it
  sub r1,#1
  jmpr cc_NE,FPI
FPOK:
  add r4,#2
  sub r6,#2
  jmpr cc_UC,MFL
MFEND:
  bclr S0TIC.7
  mov S0TBUF,#0x4B     ; 'K'
FTX:
  jnb S0TIC.7,FTX
  jmpa cc_UC,MAIN
