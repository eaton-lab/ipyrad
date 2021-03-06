#!/usr/bin/env python

""" the main CLI for calling tetrad """

from __future__ import print_function, division  # Requires Python 2.7+

from ipyrad.core.parallel import register_ipcluster
from ipyrad.assemble.utils import IPyradWarningExit, detect_cpus

import ipyparallel as ipp
import pkg_resources
import ipyrad as ip
import numpy as np
import argparse
import sys
import os
import ipyrad.analysis as ipa

# __interactive__ = False


def parse_command_line():
    """ Parse CLI args. Only three options now. """

    ## create the parser
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
  * Example command-line usage ---------------------------------------------- 

  * Read in sequence/SNP data file, provide linkage, output name, ambig option. 
     tetrad -s data.snps.phy -n test             ## input phylip and give name
     tetrad -s data.snps.phy -l data.snps.map    ## sample one SNP per locus
     tetrad -s data.snps.phy -n noambigs -r 0    ## do not use hetero sites

  * Load saved/checkpointed analysis from '.tet.json' file, or force restart. 
     tetrad -j test.tet.json -b 100         ## continue 'test' until 100 boots
     tetrad -j test.tet.json -b 100 -f      ## force restart of 'test'

  * Sampling modes: 'equal' uses guide tree to sample quartets more efficiently 
     tetrad -s data.snps.phy -m all                       ## sample all quartets
     tetrad -s data.snps.phy -m random -q 1e6 -x 123      ## sample 1M randomly
     tetrad -s data.snps.phy -m equal -q 1e6 -t guide.tre ## sample 1M across tree

  * Connect to N cores on a computer (default without -c arg is to use all avail.)
     tetrad -s data.snps.phy -c 20

  * Start an MPI cluster to connect to nodes across multiple available hosts.
     tetrad -s data.snps.phy --MPI     

  * Connect to a manually started ipcluster instance with default or named profile
     tetrad -s data.snps.phy --ipcluster        ## connects to default profile
     tetrad -s data.snps.phy --ipcluster pname  ## connects to profile='pname'

  * Further documentation: http://ipyrad.readthedocs.io/analysis.html
    """)


    ## get version from ipyrad 
    ipyversion = str(pkg_resources.get_distribution('ipyrad'))
    parser.add_argument('-v', '--version', action='version', 
        version="tetrad "+ipyversion.split()[1])

    parser.add_argument('-f', "--force", action='store_true',
        help="force overwrite of existing data")

    parser.add_argument('-s', metavar="seq", dest="seq",
        type=str, default=None,
        help="path to input phylip file (only SNPs)")

    parser.add_argument('-j', metavar='json', dest="json",
        type=str, default=None,
        help="load checkpointed/saved analysis from JSON file.")

    parser.add_argument('-m', metavar="method", dest="method",
        type=str, default="all",
        help="method for sampling quartets (all, random, or equal)")

    parser.add_argument('-q', metavar="nquartets", dest="nquartets",
        type=int, default=0,
        help="number of quartets to sample (if not -m all)")

    parser.add_argument('-b', metavar="boots", dest="boots",
        type=int, default=0,
        help="number of non-parametric bootstrap replicates")

    parser.add_argument('-l', metavar="map_file", dest="map",
        type=str, default=None,
        help="map file of snp linkages (e.g., ipyrad .snps.map)")

    parser.add_argument('-r', metavar="resolve", dest='resolve', 
        type=int, default=1, 
        help="randomly resolve heterozygous sites (default=1)")

    parser.add_argument('-n', metavar="name", dest="name",
        type=str, default="test",
        help="output name prefix (default: 'test')")

    parser.add_argument('-o', metavar="workdir", dest="workdir",
        type=str, default="./analysis-tetrad",
        help="output directory (default: creates ./analysis-tetrad)")

    parser.add_argument('-t', metavar="starting_tree", dest="tree",
        type=str, default=None,
        help="newick file starting tree for equal splits sampling")

    parser.add_argument("-c", metavar="CPUs/cores", dest="cores",
        type=int, default=0,
        help="setting -c improves parallel efficiency with --MPI")

    parser.add_argument("-x", metavar="random_seed", dest="rseed",
        type=int, default=None,
        help="random seed for quartet sampling and/or bootstrapping")    

    parser.add_argument('-d', "--debug", action='store_true',
        help="print lots more info to debugger: ipyrad_log.txt.")

    parser.add_argument("--MPI", action='store_true',
        help="connect to parallel CPUs across multiple nodes")

    parser.add_argument("--invariants", action='store_true',
        help="save a (large) database of all invariants")

    parser.add_argument("--ipcluster", metavar="ipcluster", dest="ipcluster",
        type=str, nargs="?", const="default",
        help="connect to ipcluster profile (default: 'default')")

    ## if no args then return help message
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    ## parse args
    args = parser.parse_args()

    ## RAISE errors right away for some bad argument combinations:
    if args.method not in ["random", "equal", "all"]:
        raise IPyradWarningExit(
            "method argument (-m) must be one of 'all', 'random', or 'equal'"
        )

    ## if 'random' require nquarts argument
    #if args.method == 'random':
    #    if not args.nquartets:
    #        raise IPyradWarningExit(\
    #        "  Number of quartets (-q) is required with method = random\n")

    ## if 'equal' method require starting tree and nquarts
    # if args.method == 'equal':
    #     raise IPyradWarningExit(\
    #         "  The equal sampling method is currently for developers only.\n")
    #     if not args.nquartets:
    #         raise IPyradWarningExit(\
    #         "  Number of quartets (-q) is required with method = equal\n")
    #     if not args.tree:
    #         raise IPyradWarningExit(\
    #         "  Input guide tree (-t) is required with method = equal\n")

    ## required args
    if not any(x in ["seq", "json"] for x in vars(args).keys()):
        print("""
    Bad arguments: tetrad command must include at least one of (-s or -j) 
    """)
        parser.print_help()
        sys.exit(1)

    return args



def main():
    """ main function """

    ## parse params file input (returns to stdout if --help or --version)
    args = parse_command_line()
    print(HEADER.format(ip.__version__))

    ## set random seed
    np.random.seed(args.rseed)

    # ## debugger----------------------------------------
    # if os.path.exists(ip.__debugflag__):
    #     os.remove(ip.__debugflag__)
    # if args.debug:
    #     print("\n  ** Enabling debug mode ** ")
    #     ip._debug_on()

    ## if JSON, load existing Tetrad analysis -----------------------
    if args.json:
        data = ipa.tetrad(name=args.name, workdir=args.workdir, load=True)
        ## if force then remove all results
        if args.force:
            data._refresh()

    ## else create a new tmp assembly for the seqarray-----------------
    else:
        ## create new Tetrad class Object if it doesn't exist
        newjson = os.path.join(args.workdir, args.name+'.tet.json')
        ## if not quiet...
        print("tetrad instance: {}".format(args.name))

        if (not os.path.exists(newjson)) or args.force:
            ## purge any files associated with this name if forced
            if args.force:
                ## init an object in the correct location just to refresh
                ipa.tetrad(name=args.name, 
                           workdir=args.workdir, 
                           data=args.seq, 
                           initarr=False, 
                           save_invariants=args.invariants,
                           cli=True,
                           quiet=True)._refresh()

            ## create new tetrad object
            data = ipa.tetrad(name=args.name, 
                              workdir=args.workdir, 
                              method=args.method, 
                              data=args.seq, 
                              resolve=args.resolve,
                              mapfile=args.map, 
                              guidetree=args.tree, 
                              nboots=args.boots, 
                              nquartets=args.nquartets, 
                              cli=True,
                              save_invariants=args.invariants,
                              )
        else:
            raise SystemExit(QUARTET_EXISTS
            .format(args.name, args.workdir, args.workdir, args.name, args.name)
            )

    ## boots can be set either for a new object or loaded JSON to continue it
    if args.boots:
        data.params.nboots = int(args.boots)

    ## if ipyclient is running (and matched profile) then use that one
    if args.ipcluster:
        ipyclient = ipp.Client(profile=args.ipcluster)
        data.ipcluster["cores"] = len(ipyclient)

    ## if not then we need to register and launch an ipcluster instance
    else:
        ## set CLI ipcluster terms
        ipyclient = None
        data.ipcluster["cores"] = args.cores if args.cores else detect_cpus()
        data.ipcluster["engines"] = "Local"
        if args.MPI:
            data.ipcluster["engines"] = "MPI"
            if not args.cores:
                raise IPyradWarningExit("must provide -c argument with --MPI")
        ## register to have a cluster-id with "ip- name"
        data = register_ipcluster(data)

    ## message about whether we are continuing from existing
    if data.checkpoint.boots:
        print(LOADING_MESSAGE.format(
            data.name, data.params.method, data.checkpoint.boots))

    ## run tetrad main function within a wrapper. The wrapper creates an 
    ## ipyclient view and appends to the list of arguments to run 'run'. 
    data.run(force=args.force, ipyclient=ipyclient)


## CONSTANTS AND WARNINGS
HEADER = """
-------------------------------------------------------
tetrad [v.{}] (ipyrad.analysis toolkit)
Quartet inference from phylogenetic invariants
-------------------------------------------------------\
"""

QUARTET_EXISTS = """
Error: tetrad analysis '{}' already exists in {} 
Use the force argument (-f) to overwrite old analysis files, or,
Use the JSON argument (-j {}/{}.tet.json) 
to continue analysis of '{}' from last checkpoint.
"""


LOADING_MESSAGE = """\
Continuing checkpointed analysis: {}
  sampling method: {}
  bootstrap checkpoint: {}
"""


if __name__ == "__main__": 
    main()
