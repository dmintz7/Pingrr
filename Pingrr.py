import http.client
import logging
import os
import sys
import urllib
import http.client
from logging.handlers import RotatingFileHandler
import requests
import config

from lib import sodarr
from lib import trakt

filename, file_extension = os.path.splitext(os.path.basename(__file__))
formatter = logging.Formatter('%(asctime)s - %(levelname)10s - %(module)15s:%(funcName)30s:%(lineno)5s - %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
consoleHandler = logging.StreamHandler(sys.stdout)
consoleHandler.setFormatter(formatter)
logger.addHandler(consoleHandler)
logging.getLogger("requests").setLevel(logging.WARNING)
logger.setLevel(config.log_level)
fileHandler = RotatingFileHandler(config.log_folder + '/' + filename + '.log', maxBytes=1024 * 1024 * 1, backupCount=1)
fileHandler.setFormatter(formatter)
logger.addHandler(fileHandler)

new = []
options = {"ignoreEpisodesWithFiles": False, "ignoreEpisodesWithoutFiles": False,
           "searchForMissingEpisodes": config.sonarr_search_missing_episodes}


def send_to_sonarr(a, b):
    """Send found tv program to sonarr"""

    logger.info("Attempting to send to sonarr")
    payload = {"tvdbId": a, "title": b, "qualityProfileId": config.sonarr_quality_profile, "images": [],
               "seasons": [], "seasonFolder": True, "monitored": config.sonarr_monitored,
               "rootFolderPath": config.sonarr_path_root,
               "addOptions": options,
               "tags": [config.sonarr_tag_id]
               }

    if config.pingrr_dry_run:
        logger.info("dry run is on, not sending to sonarr")
    else:
        response = None
        try:
            sdr = sodarr.API(config.sonarr_host + '/api/v3', config.sonarr_api)
            response = sdr.add_series(payload)
            logger.debug("sent to sonarr successfully")
            return True
        except Exception as a:
            logger.error('Error on line {} - {} - {}'.format(type(a).__name__, sys.exc_info()[-1].tb_lineno, a))
            logger.error("failed to send to sonarr, code return: %r", response)
            return False


def send_to_radarr(a, b, year):
    """Send found tv program to radarr"""

    logger.info("Attempting to send to radarr")

    payload = {"tmdbId": a,
               "title": b,
               "qualityProfileId": config.radarr_quality_profile,
               "images": [],
               "monitored": config.radarr_monitored,
               "titleSlug": b,
               "rootFolderPath": config.radarr_path_root,
               "minimumAvailability": config.radarr_minimumAvailability,
               "year": year,
               "addOptions": {
                   "searchForMovie": config.radarr_search
               },
               "tags": [config.sonarr_tag_id]
               }

    if config.pingrr_dry_run:
        logger.info("dry run is on, not sending to radarr")
        return True
    else:
        sdr = sodarr.API(config.radarr_host + '/api/v3', config.radarr_api)
        response = sdr.add_movie(payload)
        try:
            sdr.command({'name': 'MoviesSearch', 'movieIds': [response['id']]})
            logger.debug("sent to radarr successfully")
            return True
        except Exception as a:
            logger.error('Error on line {} - {} - {}'.format(type(a).__name__, sys.exc_info()[-1].tb_lineno, a))
            logger.error("failed to send to radarr")
            return False


def add_media(program):
    added_list = []
    for media in new:
        media_id = None
        title = media['title']

        if program == "radarr":
            media_id = media['tmdb']
        elif program == "sonarr":
            media_id = media['tvdb']

        if media_id:
            try:
                logger.debug('Sending media to {}: {}'.format(program, media['title']))
                if program == "sonarr":
                    if send_to_sonarr(media_id, title):
                        logger.info('{} has been added to Sonarr'.format(title))
                        added_list.append(media['title'])
                if program == "radarr":
                    if send_to_radarr(media_id, title, media['year']):
                        logger.info('{} has been added to Radarr'.format(title))
                        added_list.append(media['title'])
            except IOError:
                logger.warning('error sending media: {} id: {}'.format(title, str(media_id)))
          
        if config.pushover_enabled:
            message = "The following {} item(s) out of {} added to {}:\n{}".format(str(len(added_list)), str(len(new)), program, "\n".join(added_list))
            send_message(message)

    if config.pushover_enabled:
        message = "The following {} item(s) out of {} added to {}:\n{}".format(str(len(added_list)), str(len(new)), program, "\n".join(added_list))
        send_message(message)


def new_check(item_type):
    """Check for new trakt items in list"""
    if item_type == "movies":
        library = sodarr.get_radarr_library()
        program = "radarr"
    else:
        library = sodarr.get_sonarr_library()
        program = "sonarr"

    global new

    new = filter_list(item_type)
    logger.info('checking for new {} in lists'.format(item_type))

    if item_type == "movies":
        item_id = "imdb"
    else:
        item_id = "tvdb"

    for x in new:
        logger.debug('checking {} from list: {}'.format(item_type, x['title']))
        if x[item_id] not in library and config.filters_allow_ended:
            logger.info('new media found, adding {} {} now'.format(len(new), item_type))
            add_media(program)
            break

        if item_type == "shows":
            if x[item_id] not in library and not x['status'] == 'ended':
                logger.info('new continuing show(s) found, adding shows now')
                add_media(program)
                break


def check_lists(arg, arg2):
    for filters in arg:
        for data in arg2:
            if filters == data:
                return True
    return False


def filter_check(title, item_type):
    if item_type == "shows":
        if len(title['country']):
            country = title['country'].lower()
        else:
            country = False
        type_id = "tvdb"
        library = sonarr_library
    elif item_type == "movies":
        type_id = "tmdb"
        library = radarr_library
        country = False
    else:
        return False

    lang = title['language']

    if title[type_id] not in library:
        logger.debug("Checking year: {}".format(title['year']))
        if config.filters_year[item_type] > title['year']:
            logger.info(
                "{} was rejected as it was outside allowed year range: {}".format(title['title'], str(title['year'])))
            return False

        logger.debug("Checking runtime: {}".format(title['runtime']))
        if config.filters_runtime > title['runtime']:
            logger.info(
                "{} was rejected as it was outside allowed runtime: {}".format(title['title'], str(title['runtime'])))
            return False

        if item_type == "shows":
            if len(config.filters_network) > 0:
                if title['network'] is None or title['network'] in config.filters_network:
                    logger.info("{} was rejected as it was by a disallowed network: {}".format(title['title'], str(title['network'])))
                    return False
        logger.debug("Checking votes: {}".format(title['votes']))

        if config.filters_votes > title['votes']:
            logger.info(
                "{} was rejected as it did not meet vote requirement: {}".format(title['title'], str(title['votes'])))
            return False

        if config.filters_allow_ended is False and 'ended' in title['status']:
            logger.info("{} was rejected as it is an ended tv series".format(title['title']))
            return False

        if item_type == "shows":
            if config.filters_allow_canceled is False and 'canceled' in title['status']:
                logger.info("{} was rejected as it an canceled tv show".format(title['title']))
                return False

        logger.debug("Checking rating: {}".format(title['rating']))
        if float(title['rating']) < float(config.filters_rating):
            logger.info("{} was rejected as it was outside the allowed ratings: {}".format(title['title'], str(title['rating'])))
            return False

        logger.debug("Checking genres: {}".format(title['genres']))
        if isinstance(config.filters_genre, list):
            if check_lists(config.filters_genre, title['genres']):
                logger.info(
                    "{} was rejected as it wasn't a wanted genre: {}".format(title['title'], str(title['genres'])))
                return False
        elif title['genres'] in config.filters_genre:
            logger.info("{} was rejected as it wasn't a wanted genre: {}".format(title['title'], str(title['genres'])))
            return False

        logger.debug("Checking country: {}".format(country))
        if country and country not in config.filters_country:
            logger.info("{} was rejected as it wasn't a wanted country: {}".format(title['title'],
                                                                                   str(title['country'])))
            return False

        logger.debug("Checking language: {}".format(lang))
        if lang not in config.filters_language:
            logger.info("{} was rejected as it wasn't a wanted language: {}".format(title['title'], lang))
            return False
        return True

    else:
        logger.info("{} was rejected as it is already in {} library".format(title['title'], item_type))


def filter_list(list_type):
    # Create the lists ready to be filtered down
    item_id = None
    raw_list = []
    if list_type == 'shows':
        item_id = "tvdb"
        for trakt_list in config.trakt_tv_list:
            if config.trakt_tv_list[trakt_list]:
                raw_list = trakt.get_info('tv')
                break
    if list_type == 'movies':
        item_id = "tmdb"
        for trakt_list in config.trakt_movie_list:
            if config.trakt_movie_list[trakt_list]:
                raw_list = trakt.get_info('movie')
                break
        fixed_raw = []
        for raw in raw_list:
            try:
                fixed_raw.append(raw[0])
            except KeyError:
                fixed_raw.append(raw)
        raw_list = fixed_raw

    filtered = []
    for title in raw_list:
        try:
            # If not already in the list, check against filters
            if filter_check(title, list_type) and title[item_id] not in filtered:
                logger.info('adding {} to potential add list'.format(title['title']))
                filtered.append(title)
        except TypeError:
            logger.debug('{} failed to check against filters'.format(title['title']))

    logger.debug("Filtered list successfully")

    return filtered


def send_message(message):
    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request("POST", "/1/messages.json", urllib.parse.urlencode({"token": config.pushover_app_token, "user": config.pushover_user_key, "message": message}), {"Content-type": "application/x-www-form-urlencoded"})
    conn.getresponse()


if __name__ == "__main__":
    logger.info("###### Checking if TV lists are wanted ######")

    if config.sonarr_api:
        try:
            sonarr_library = sodarr.get_sonarr_library()
            new_check('shows')
        except requests.exceptions.ReadTimeout:
            logger.warning("Sonarr library timed out, skipping for now")
        except requests.exceptions.ConnectionError:
            logger.warning("Can not connect to Sonarr, check sonarr is running or host is correct")
        except Exception as e:
            logger.error('Error on line {}, {}, {}'.format(sys.exc_info()[-1].tb_lineno, type(e).__name__, e))
            raise

    logger.info("###### Checking if Movie lists are wanted ######")
    if config.radarr_api:
        try:
            radarr_library = sodarr.get_radarr_library()
            new_check('movies')
        except requests.exceptions.ReadTimeout:
            logger.warning("Radarr library timed out, skipping for now")
        except requests.exceptions.ConnectionError:
            logger.warning("Can not connect to Radarr, check Radarr is running or host is correct")
        except Exception as e:
            logger.error('Error on line {}, {}, {}'.format(sys.exc_info()[-1].tb_lineno, type(e).__name__, e))
    logger.info("check finish")
