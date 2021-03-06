#!/usr/bin/env python

"convert VCF to database format for SNP analyses"

# py2/3 compat
from __future__ import print_function, division
from builtins import range

import os
import tempfile

import h5py 
import numpy as np
import pandas as pd
from ..assemble.utils import TRANSFULL, IPyradError


class VCFtoHDF5(object):
    """
    Creates a temporary snps.hdf5 file conversion of the VCF file.
    For ipyrad assembled RAD seq data this will use RAD loci as the 
    grouping of SNPs into linkage blocks for subsampling. If VCF is from 
    some other assembly (e.g., WGS) then you can use the ld_block_size arg to 
    group SNPs into linkage blocks for subsampling analyses.
    """
    def __init__(
        self, 
        data,
        name="test", 
        workdir="./analysis-vcf2hdf5", 
        ld_block_size=None,
        quiet=False,
        ):

        # attrs
        self.data = data
        self.name = (name if name else "test")
        self.workdir = (workdir if workdir else tempfile.gettempdir())
        self.names = []
        self.nsamples = 0
        self.nsnps = 0
        self.hlines = 0
        self.ld_block_size = ld_block_size
        self.database = ""
        self.quiet = quiet 

        # check for data file
        self.database = os.path.join(self.workdir, self.name + ".snps.hdf5")
        assert os.path.exists(self.data), "file {} not found".format(self.data)
        if not os.path.exists(self.workdir):
            os.makedirs(self.workdir)

        # vcf format info
        self.source = ""
        self.reference = ""


    def run(self):
        """
        Parse and convert data to HDF5 file format. 
        """
        # print message
        self._print("Indexing VCF to HDF5 database file")

        # get sample names, count header lines and nsnps
        self.get_meta()

        # init the database to fill
        self.init_database()

        # fill snps matrix
        self.build_matrix()

        # report on new database
        self._print(
            "HDF5: {} snps; {} scaffolds; {} linkage group"
            .format(
                self.df.shape[0], 
                len(set(self.df["#CHROM"])), 
                len(set(self.df["BLOCK"])),
            ))
        self._print(
            "SNP database written to {}"
            .format(self.database)
        )


    def _print(self, msg):
        if not self.quiet:
            print(msg)


    def get_meta(self):
        """
        Skip and count ## lines, and get names from first # line in VCF.
        """
        # store a list of chrom names
        chroms = set()

        if self.data.endswith(".gz"):
            import gzip
            infile = gzip.open(self.data)
        else:
            infile = open(self.data)

        # get data header line
        for dat in infile:

            # split on space
            data = dat.decode().split()

            # parse meta data lines
            if data[0][0] == "#":
                if data[0][1] != "#":
                    # parse names from header line in their order
                    self.names = data[9:]
                    self.nsamples = len(self.names)
                else:
                    # store header line count and look for format str
                    self.hlines += 1
                    if "source=" in data[0].lower():
                        self.source = data[0].split("source=")[-1].strip()
                    if "reference=" in data[0].lower():
                        self.reference = data[0].split("reference=")[-1].strip()

            # meta snps data
            else:
                self.nsnps += 1
                chroms.add(data[0])

        # close file handle
        infile.close()

        # convert chroms into a factorized list
        self._print("VCF: {} SNPs; {} scaffolds".format(self.nsnps, len(chroms)))        


    def init_database(self):
        """
        # load vcf file as a pandas dataframe in chunks.
        """
        # init the database file
        with h5py.File(self.database, 'w') as io5:

            # core data sets
            io5.create_dataset("genos", (self.nsnps, self.nsamples, 2), "u1")
            io5.create_dataset("snps", (self.nsamples, self.nsnps), "u1")
            io5.create_dataset("snpsmap", (self.nsnps, 5), "u4")
            io5["snps"].attrs["names"] = [i.encode() for i in self.names]
            io5["genos"].attrs["names"] = [i.encode() for i in self.names]


    def build_chunked_matrix(self):
        """
        Fill HDF5 database with VCF data in chunks at a time.
        """
        self.df = pd.read_csv(
            self.data, 
            sep="\t", 
            skiprows=self.hlines, 
            chunksize=int(1e6),
        )

        # iterate over chunks of the file
        for chunkdf in self.df:

            genos, snps, snpmap = jfill_arrays_from_df()



    def build_matrix(self):
        """
        Fill database with VCF data
        """

        # load vcf as dataframe (chunking makes this an iterator)
        self.df = pd.read_csv(self.data, sep="\t", skiprows=self.hlines)

        # get ref and alt alleles as a string series
        refalt = (self.df.REF + self.df.ALT).apply(str.replace, args=(",", ""))

        # genos array to fill from geno indices and refalt
        genos = np.zeros((self.nsnps, self.nsamples, 2), dtype="u1")
        snps = np.zeros((self.nsnps, self.nsamples), dtype="S1")
        snpsmap = np.zeros((self.nsnps, 5), dtype="u4")

        # iterate over samples indices
        for sidx in range(self.nsamples):

            # store geno calls for this sample (+9 goes to geno columns in vcf)
            glist = self.df.iloc[:, sidx + 9].apply(get_genos).apply(sorted)
            genos[:, sidx, :] = pd.concat([
                glist.apply(lambda x: x[0]),
                glist.apply(lambda x: x[1]),
            ], axis=1)

            # iterate over geno indices to get alleles
            for gidx in range(genos.shape[0]):

                # this sample's geno
                sgeno = genos[gidx, sidx]
                genos[gidx, sidx] = sgeno
                
                # bail if geno is missing (TODO: or indel).
                if sgeno[0] == 9:
                    call = b"N"
                    continue

                # convert to geno alleles
                call0 = refalt[gidx][sgeno[0]]
                call1 = refalt[gidx][sgeno[1]]

                # get ambiguity code
                if call0 != call1:
                    call = TRANSFULL[(call0, call1)].encode()
                else:
                    call = call0

                # store call
                snps[gidx, sidx] = call

        # convert snps to uint8
        snps = snps.view("u1").T

        # snpsmap: if ipyrad denovo it's easy, and they should just use hdf5.
        if ("ipyrad" in self.source) and ("pseudo-ref" in self.reference):
            snpsmap[:, 0] = self.df["#CHROM"].factorize()[0].astype("u4") + 1
            snpsmap[:, 1] = np.concatenate(
                [range(i[1].shape[0]) for i in self.df.groupby("#CHROM")])
            snpsmap[:, 2] = self.df.POS
            snpsmap[:, 3] = 0
            snpsmap[:, 4] = range(snpsmap.shape[0])
            self.df["BLOCK"] = self.df["#CHROM"]

        # snpsmap: if ipyrad ref VCF the per-RAD loc info is available too
        elif "ipyrad" in self.source:

            # skip to generic vcf method if ld_block_size is set:
            if not self.ld_block_size:
                snpsmap[:, 0] = self.df["#CHROM"].factorize()[0]
                snpsmap[:, 1] = np.concatenate(
                    [range(i[1].shape[0]) for i in self.df.groupby("#CHROM")])
                snpsmap[:, 2] = self.df.POS
                snpsmap[:, 3] = 0
                snpsmap[:, 4] = range(snpsmap.shape[0])
                self.df["BLOCK"] = self.df["#CHROM"]

        # snpsmap: for other program's VCF's we need ldsize arg to chunk.
        else:
            if not self.ld_block_size:
                raise IPyradError(
                    "You must enter an ld_block_size estimate for this VCF.")

        # (TODO: numpy/numba this loop instead of pand)
        # cut it up by block size (unless it's denovo, then skip.)
        if (self.ld_block_size) and ("pseudo-ref" not in self.reference):

            # block and df index counters
            bidx = 0
            dfidx = 0

            # iterate over existing scaffolds
            for _, scaff in self.df.groupby("#CHROM"):

                # start and end of this scaffold
                gpos = scaff.iloc[0, 1]
                end = scaff.POS.max()

                # iterate to break scaffold into linkage blocks
                while 1:

                    # grab a block
                    mask = (scaff.POS >= gpos) & (scaff.POS < gpos + self.ld_block_size)
                    block = scaff[mask]

                    # check for data and sample a SNP
                    if block.size:
                        
                        # enter new block into dataframe
                        self.df.loc[dfidx:dfidx + block.shape[0], "BLOCK"] = bidx
                        dfidx += block.shape[0]
                        bidx += 1

                    # advance counter
                    gpos += self.ld_block_size

                    # break on end of scaff
                    if gpos > end:
                        break
                
            # store it (CHROMS/BLOCKS are stored 1-indexed !!!!!!)
            snpsmap[:, 0] = self.df.BLOCK + 1
            snpsmap[:, 1] = np.concatenate(
                [range(i[1].shape[0]) for i in self.df.groupby("BLOCK")])
            snpsmap[:, 2] = self.df.POS
            snpsmap[:, 3] = self.df["#CHROM"].factorize()[0] + 1
            snpsmap[:, 4] = range(self.df.shape[0])

        # store to database
        with h5py.File(self.database, 'a') as io5:
            io5['snps'][:] = snps
            io5['genos'][:] = genos
            io5['snpsmap'][:] = snpsmap
        del snps



def get_genos(gstr):
    """
    Extract geno from vcf string: e.g., (0/1:...). 
    First pulls the indices then uses refalt to pull genos.
    """
    return gstr[0], gstr[2]



def return_g(g, i):
    "returns the genotype str from vcf at one position (0/1) -> 0"
    return g[i]

# vectorized version of return g
v_return_g = np.vectorize(return_g, otypes=[np.int8])


def chunk_to_arrs(chunkdf, nsamples):

    # nsnps in this chunk
    nsnps = chunkdf.shape[0]

    # chrom, pos as factor, integers
    arrpos = chunkdf.values[:, [0, 1]]
    arrpos[:, 0] = pd.factorize(arrpos[:, 0])[0]
    arrpos = arrpos.astype(np.int64)

    # base calls as int8
    ref = chunkdf.values[:, 3].astype(bytes)
    alts = chunkdf.values[:, 4].astype(bytes)
    sas = np.char.replace(alts, b",", b"")
    alts1 = np.zeros(alts.size, dtype=bytes)
    alts2 = np.zeros(alts.size, dtype=bytes)
    alts3 = np.zeros(alts.size, dtype=bytes)
    lens = np.array([len(i) for i in sas])
    alts1[lens == 1] = [i[0] for i in sas[lens == 1]]
    alts2[lens == 2] = [i[1] for i in sas[lens == 2]]
    alts3[lens == 3] = [i[2] for i in sas[lens == 3]]

    # genotypes as int8 
    g0 = v_return_g(chunkdf.values[:, 9:], 0)
    g1 = v_return_g(chunkdf.values[:, 9:], 2)

    # TODO: all below here could be numba compiled if using ints (and dict workaround)

    # genos array to fill from geno indices and refalt
    genos = np.zeros((nsnps, nsamples, 2), dtype="u1")
    snps = np.zeros((nsnps, nsamples), dtype="S1")
    snpsmap = np.zeros((nsnps, 5), dtype="u4")

    # fill genos with sum of calls except missing to 9
    genos[:, :, 0] = g0
    genos[:, :, 1] = g1

    # fill snps in rows by indexing genos from ref,alt with g0,g1
    for ridx in range(snps.shape[0]):
        
        # missing values are N=78
        snps[ridx, g0[ridx] == 9] = b"N"
        
        # 0/0 put in the ref allele at each row
        snps[ridx, (g0[ridx] + g1[ridx]) == 0] = ref[ridx]
        
        # 1/1 put in the alt0 allele
        snps[ridx, ((g0 == 1) & (g1 == 1))[ridx]] = alt1[ridx]

        # 2/2 put in the alt1 allele
        snps[ridx, ((g0 == 2) & (g1 == 2))[ridx]] = alt2[ridx]

        # 3/3 put in the alt2 allele
        snps[ridx, ((g0 == 3) & (g1 == 3))[ridx]] = alt3[ridx]    

    # fill ambiguity sites 
    ambs = np.where(g0 != g1)
    for idx in range(ambs[0].size):

        # row, col indices of the ambiguous site in the snps mat
        row = ambs[0][idx]
        col = ambs[1][idx]
        
        # get genos (0123) from the geno matrices
        a0 = g0[row, col]
        a1 = g1[row, col]        
        alls = sorted([a0, a1])
        
        # get the alleles (CATG) from the ref/alt matrices
        if alls[0] == 0:
            b0 = ref[row]
            if alls[1] == 1:
                b1 = alt1[row]
            elif alls[1] == 2:
                b1 = alt2[row]
            else:
                b1 = alt3[row]
        elif alls[0] == 1:
            b0 = alt1[row]
            if alls[1] == 2:
                b1 = alt2[row]
            else:
                b1 = alt3[row]
        elif alls[0] == 2:
            b0 = alt2[row]
            b1 = alt3[row]
        
        # convert allele tuples into an ambiguity byte
        snps[row, col] = TRANSFULL[(b0, b1)].encode()


    # return the three arrays
    return genos, snps, snpsmap

