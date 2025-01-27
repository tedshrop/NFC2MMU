#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024 Sebastian Andersson <sebastian@bittr.nu>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Program to set current filament & spool in klipper, and write to tags. """

import logging
import os
import sys
import shutil
import threading
from pathlib import Path

from flask import Flask, render_template
import toml

from lib.moonraker_web_client import MoonrakerWebClient
from lib.nfc_handler import NfcHandler
from lib.spoolman_client import SpoolmanClient

SPOOL = "SPOOL"
FILAMENT = "FILAMENT"
NDEF_TEXT_TYPE = "urn:nfc:wkt:T"

CFG_DIR = "~/.config/nfc2klipper"

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s %(levelname)s - %(name)s: %(message)s"
)

args = None  # pylint: disable=C0103
for path in ["~/nfc2klipper.cfg", CFG_DIR + "/nfc2klipper.cfg"]:    #get the full path name from the config path?
    cfg_filename = os.path.expanduser(path)                         #set a variable with the full path name including the user.
    if os.path.exists(cfg_filename):                                #if the file exists,
        with open(cfg_filename, "r", encoding="utf-8") as fp:       #open it and
                args = toml.load(fp)                                #read the values
                break                                               #end the for loop

if not args:                                                        #if the file was empty
    print(
        "WARNING: The config file is missing, installing a default version.",
        file=sys.stderr,
    )
    if not os.path.exists(CFG_DIR):                                 #if the directory doesn't exist
        cfg_dir = os.path.expanduser(CFG_DIR)                       #create a full path name with the user
        print(f"Creating dir {cfg_dir}", file=sys.stderr)           #tell the console what's happening
        Path(cfg_dir).mkdir(parents=True, exist_ok=True)            #make the folder
    script_dir = os.path.dirname(__file__)                          #get the path name of where we are
    from_filename = os.path.join(script_dir, "nfc2klipper.cfg")     #add the filename to said path
    to_filename = os.path.join(cfg_dir, "nfc2klipper.cfg")          #add the filename to the destination path.
    shutil.copyfile(from_filename, to_filename)                     #copy the file
    print(f"Created {to_filename}, please update it", file=sys.stderr)
    sys.exit(1)                                                     #stop the program until the config has been updated.

#assuming we got some values from the config:
spoolman = SpoolmanClient(args["spoolman"]["spoolman-url"])         #set spoolman url from the config file
moonraker = MoonrakerWebClient(args["moonraker"]["moonraker-url"])  #set moonraker url from the config file
nfc_handler = NfcHandler(args["nfc"]["nfc-device"])                 #set the port that the reader is connected to.
mmu_enable = args['mmu']


app = Flask(__name__)                                               #create the web application


def set_spool_and_filament(spool: int, filament: int):              #tells moonraker 
    """Calls moonraker with the current spool & filament"""

    if "old_spool" not in set_spool_and_filament.__dict__:          #if the old filament is not in the dictionary,
        set_spool_and_filament.old_spool = None                     #set the old_spool to none
        set_spool_and_filament.old_filament = None                  #set the old_spool to none

    if (                                                            #if the spool is the same and
        set_spool_and_filament.old_spool == spool
        and set_spool_and_filament.old_filament == filament         #the filament is the same,
    ):
        app.logger.info("Read same spool & filament")               #log it and
        return                                                      #end the function

    app.logger.info("Sending spool #%s, filament #%s to klipper", spool, filament) #log the filament change

    set_spool_and_filament.old_spool = None                         #set the old_spool to none, just in case this fails.  
    set_spool_and_filament.old_filament = None                      #set the old_filament to none

    try:                                                            #try
        moonraker.set_spool_and_filament(spool, filament,0)         #setting the gate's spool ID via gcode
    except Exception as ex:  # pylint: disable=W0718
        app.logger.error(ex)
        return

    set_spool_and_filament.old_spool = spool                        #set the old_spool for checking if there was a change (above)
    set_spool_and_filament.old_filament = filament                  #set the old_filament for checking if there was a change (above)


@app.route("/w/<int:spool>/<int:filament>")                         #create web page for filament
def write_tag(spool, filament):                                     #create write_tag function
    """
    The web-api to write the spool & filament data to NFC/RFID tag
    """
    app.logger.info("  write spool=%s, filament=%s", spool, filament) #log it
    if nfc_handler.write_to_tag(spool, filament):                   #write the values to the tag    
        return "OK"                                                 #finish up
    return ("Failed to write to tag", 502)                          #return an error if it failed


@app.route("/")                                                     #main web page
def index():                                                        #define it
    """
    Returns the main index page.
    """
    spools = spoolman.get_spools()                                  #get a list of spools from the spoolman server     

    return render_template("index.html", spools=spools)             #show the list of spools on the website


def should_clear_spool() -> bool:                               
    """Returns True if the config says the spool should be cleared"""
    if args["moonraker"].get("clear_spool"):
        return True
    return False


def on_nfc_tag_present(spool, filament):
    """Handles a read tag"""

    if not should_clear_spool():                                    #if we're not clearing tags        
        if not (spool and filament):                                #and there's not both values present,
            app.logger.info("Did not find spool and filament records in tag")#log it
    if should_clear_spool() or (spool and filament):                #if we are clearing tags, or there's new values present,
        if not spool:                                               #if we're clearing the spool,
            spool = 0                                               #clear it
        if not filament:                                            #if we're clearing the spool,
            filament = 0                                            #clear it    
        set_spool_and_filament(spool, filament)                     #set the filament via moonraker


def on_nfc_no_tag_present():
    """Called when no tag is present (or tag without data)"""
    if should_clear_spool():
        set_spool_and_filament(0, 0)


if __name__ == "__main__":                                          #main function that runs everything.

    if should_clear_spool():                                        #if we're clearing spools,
        set_spool_and_filament(0, 0)                                # Start by unsetting current spool & filament

    nfc_handler.set_no_tag_present_callback(on_nfc_no_tag_present)  #set up the dection for the tag being removed?
    nfc_handler.set_tag_present_callback(on_nfc_tag_present)        #set up the dection for the tag being presented?

    if not args["webserver"].get("disable_web_server"):             #if we are starting the web server,
        app.logger.info("Starting nfc-handler")                     #log it    
        thread = threading.Thread(target=nfc_handler.run)           #set the nfc_handler to run on the back end
        thread.daemon = True                                        
        thread.start()                                              #start it

        app.logger.info("Starting web server")                      #log it
        try:                                                        #try running
            app.run(                                                #the web app
                args["webserver"]["web_address"], port=args["webserver"]["web_port"]    #with these arguments
            )
        except Exception:                                           #if it fails
            nfc_handler.stop()                                      #stop the background task
            thread.join()                                           #bring it to the foreground
            raise                                                   #raise and exception    
    else:                                                           #if we aren't running the web server
        nfc_handler.run()                                           #just run the nfc hander on the front end.
