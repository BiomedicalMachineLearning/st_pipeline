#!/usr/bin/env python
"""
    Copyright (C) 2012  Spatial Transcriptomics AB,
    read LICENSE for licensing terms. 
    Contact : Jose Fernandez Navarro <jose.fernandez.navarro@scilifelab.se>

"""

""" This class contains wrappers to make systems calls for different annotation tools
most of the options can be passed as arguments
"""

import logging
import subprocess
import os
import sys
from itertools import izip
from main.common.fastq_utils import *
from main.common.utils import *
import HTSeq

    
def annotateReadsWithHTSeq(samFile, gtfFile, mode, outputFolder=None):
    ''' Annotates the reads using htseq-count tool.
        Input: a SAM file with reads, a GTF file with HTSeq annotations.
        Output a SAM file with annotations.
    '''
    logger = logging.getLogger("STPipeline")
    
    if samFile.endswith(".sam"):
        outputFile = replaceExtension(getCleanFileName(samFile),'_gene.sam')
        if outputFolder is not None and os.path.isdir(outputFolder): 
            outputFile = os.path.join(outputFolder, outputFile)
    else:
        logger.error("Error: Input format not recognized " + samFile)
        raise RuntimeError("Error: Input format not recognized " + samFile + "\n")
    
    logger.info("Start Annotating reads with HTSeq")
    
    discard_output = open(os.devnull,"w")
    
    #-q (suppress warning reports)
    #-a (min quality)
    #-f (format)
    #-m (annotation mode)
    #-s (strandeness)
    #-i (attribute in GTF to be used as ID)
    #-t (feature type to be used in GTF)
    #-r (input sorted order : name - pos)
    ##TODO make sure the sorting is correct and not affecting results
    ##TODO make sure having a fw or rw missing in a pair is not affecting results
    args = ['htseq-count',"-r", "name", "-q", "-a", "0", "-f", "sam", "-m" , mode, "-s", "no", "-t", 
            "exon", "-i","gene_id" , "-o", outputFile, samFile, gtfFile]
    subprocess.check_call(args,stdout=discard_output, stderr=subprocess.PIPE)
    
    if not fileOk(outputFile):
        logger.error("Error: output file is not present " + outputFile)
        raise RuntimeError("Error: output file is not present " + outputFile + "\n")
        
    logger.info("Finish Annotating reads with HTSeq")
    
    return outputFile


def getAllMappedReadsBed(mapWithGeneFile):
    ''' creates a map with the read names that are annotated and mapped and 
        their mapping scores, chromosome and gene
    '''
    #@todo check input format and correctness, same for output
    logger = logging.getLogger("STPipeline")
    mapped = dict()
    inF = getCleanFileName(safeOpenFile(mapWithGeneFile,'r'))
    dropped = 0
    for line in inF:
        cols = line.rstrip().split('\t')
        try:
            cleanHeader = str(cols[3])
            mapping_quality = int(cols[4])
            gene_name = str(cols[18])
            chromosome = str(cols[0])
            if gene_name != '.': 
                mapped[cleanHeader] = (mapping_quality,gene_name,chromosome)  # there should not be collisions
            else:
                dropped += 1
        except ValueError:
            dropped += 1
            pass

    inF.close()
    logger.info("Created map of annotated reads, dropped : " + str(dropped) + " reads")  
    return mapped


def getAllMappedReadsSam(annot_reads, htseq_no_ambiguous = False):
    ''' creates a map with the read names that are annotated and mapped and 
        their mapping scores, chromosome and gene.
        We assume the gtf file has its gene IDs replaced by gene names
    '''
    
    logger = logging.getLogger("STPipeline")
    
    filter_htseq = ["__no_feature","__ambiguous",
              "__too_low_aQual","__not_aligned",
              "__alignment_not_unique"]
    
    mapped = dict()
    sam = HTSeq.SAM_Reader(annot_reads)
    dropped = 0
    for alig in sam:
        
        gene_name = str(alig.optional_field("XF"))
        if gene_name in filter_htseq or not alig.aligned or \
        (htseq_no_ambiguous and gene_name.find("__ambiguous") != -1):
            dropped += 1
            continue
        
        strand = str(alig.pe_which)
        name = str(alig.read.name) 
        #seq = str(alig.read.seq)
        #qual = str(alig.read.qualstr)
        mapping_quality = int(alig.aQual)
        
        if alig.mate_start:
            chromosome = alig.mate_start.chrom 
        else:
            chromosome = "Unknown"
        
        #add this to distinct first to second pair in the HASH since read names are the same
        if strand == "first":
            name += "/1"
        elif strand == "second":
            name += "/2"
        else:
            dropped += 1
            continue ## not possible
        
        mapped[name] = (mapping_quality,gene_name,chromosome)  # there should not be collisions
      
    logger.info("Created map of annotated reads, dropped : " + str(dropped) + " reads")  
    return mapped


def getAnnotatedReadsFastq(annot_reads, fw, rv, htseq_no_ambiguous = False, outputFolder=None):  
    ''' Gets the forward and reverse reads, qualities and sequences that are annotated
        and mapped (present in annot_reads)
    '''
    
    logger = logging.getLogger("STPipeline")
    
    if  annot_reads.endswith(".sam")  \
        and fw.endswith(".fastq") and rv.endswith(".fastq"):
        outputFile = replaceExtension(getCleanFileName(fw),'_withTranscript.fastq')
        if outputFolder is not None and os.path.isdir(outputFolder): 
            outputFile = os.path.join(outputFolder, outputFile)
    else:
        logger.error("Error: Input format not recognized " + annot_reads + " , " + fw + " , " + rv)
        raise RuntimeError("Error: Input format not recognized " + annot_reads + " , " + fw + " , " + rv + "\n")

    logger.info("Start Mapping to Transcripts")
    
    mapped = getAllMappedReadsSam(annot_reads, htseq_no_ambiguous)

    if len(mapped) == 0:
        logger.error("Error: Annotation file not recognized, no records present")
        raise RuntimeError("Error: Annotation file not recognized, no records present")
    else:
        readMappingToTranscript = len(mapped)
    
    outF = safeOpenFile(outputFile,'w')
    outF_writer = writefq(outF)
    fw_file = safeOpenFile(fw, "rU")
    rv_file = safeOpenFile(rv, "rU")
    
    #from the raw fw and rv reads write the records that have been mapped and annotated 
    for line1,line2 in izip( readfq(fw_file) , readfq(rv_file) ):

        #bowtie2 will truncate white spaces in header name according to SAM format
        raw_name1 = line1[0].split(" ")[0]
        raw_name2 = line2[0].split(" ")[0]
        
        #we add this to match the reads to the extra symbol added in the annotation
        #TODO the find() could be removed
        name1 = (raw_name1 + "/1") if raw_name1.find("/1") == -1 else raw_name1
        name2 = (raw_name2 + "/2") if raw_name2.find("/2") == -1 else raw_name2
        mappedFW = mapped.has_key(name1)
        mappedRV = mapped.has_key(name2)
        
        if mappedFW and mappedRV: #fw and rv mapped and annotated
            
            if mapped[name1][0] > mapped[name2][0]: #pick highest scored
                new_line1 = ( (name1 + " Chr:" +  mapped[name1][2] + " Gene:" + mapped[name1][1]), line1[1], line1[2] )
                outF_writer.send(new_line1)
            else:
                new_line2 = ( (name2 + " Chr:" +  mapped[name2][2] + " Gene:" + mapped[name2][1]), line2[1], line2[2] ) 
                outF_writer.send(new_line2)
                
        elif mappedFW:  # only fw mapped and annotated    
            new_line1 = ( (name1 + " Chr:" +  mapped[name1][2] + " Gene:" + mapped[name1][1]), line1[1], line1[2] )        
            outF_writer.send(new_line1)    
            
        elif mappedRV:
            new_line2 = ( (name2 + " Chr:" +  mapped[name2][2] + " Gene:" + mapped[name2][1]), line2[1], line2[2] )
            outF_writer.send(new_line2)  # only rv mapped and annotated
            
        else:
            pass
            #neither fw or rw are annotated and mapped

    outF_writer.close()
    outF.close()
    fw_file.close()
    rv_file.close()
    
    if not fileOk(outputFile):
        logger.error("Error: output file is not present " + outputFile)
        raise RuntimeError("Error: output file is not present " + outputFile + "\n")
    else:
        logger.info('Total reads mapping to a transcript : ' + str(readMappingToTranscript))
        
    logger.info("Finish Mapping to Transcripts")
        
    return outputFile