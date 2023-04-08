import os
import sys
import logging
import requests
import tempfile
import re
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from iyp import BaseCrawler
import neo4j.exceptions


def get_latest_dataset_url(inetintel_data_url: str, file_name_format: str):
    pattern = re.compile(r'^\d{4}-\d{2}$')
    response = requests.get(inetintel_data_url)
    soup = BeautifulSoup(response.text, 'html.parser')
    date_elements = soup.find_all("a", string=pattern)
    all_date = []
    for date_element in date_elements:
        all_date.append(date_element.text)
    latest_date = datetime.strptime(all_date[-1], '%Y-%m')
    dateset_file_name: str = latest_date.strftime(file_name_format)
    inetintel_data_url: str = inetintel_data_url.replace('github.com', 'raw.githubusercontent.com')
    inetintel_data_url: str = inetintel_data_url.replace('tree/', '')
    full_url: str = f'{inetintel_data_url}/{all_date[-1]}/{dateset_file_name}'
    return full_url


# Organization name and URL to data
ORG = 'Internet Intelligence Lab'
URL = get_latest_dataset_url('https://github.com/InetIntel/Dataset-AS-to-Organization-Mapping/tree/master/data',
                             'ii.as-org.v01.%Y-%m.json')
NAME = 'inetintel.siblings_asdb'  # should reflect the directory and name of this file


class Crawler(BaseCrawler):
    # Base Crawler provides access to IYP via self.iyp
    # and set up a dictionary with the org/url/today's date in self.reference

    def run(self):
        """Fetch data and push to IYP. """

        # Create a temporary directory
        tmpdir = tempfile.mkdtemp()

        # Filename to save the JSON file as
        filename = os.path.join(tmpdir, 'siblings_asn_dataset.json')

        # Fetch data
        try:
            req = requests.get(URL)
        except requests.exceptions.ConnectionError as e:
            logging.error(e)
            sys.exit('Connection error while fetching data file')
        except requests.exceptions.HTTPError as e:
            logging.error(e)
            sys.exit('Error while fetching data file')

        with open(filename, "w") as file:
            file.write(req.text)

        # The dataset is very large. Pandas has the ability to read JSON, and, in theory, it could do it in a more
        # memory-efficient way.
        df = pd.read_json(filename, orient='index')

        # Use df.head() to read the first 100 entries in the JSON dataset
        # df_10 = df.head(10)

        lines = []
        asns = set()
        sibling_asns = set()
        urls = set()

        for index, row in df.iterrows():
            asn = str(index)
            asns.add(asn)
            for sibling_asn in row['Sibling ASNs']:
                sibling_asns.add(sibling_asn)
            url = row['Website']
            if len(url) > 1:
                urls.add(url)
            lines.append([asn, url, sibling_asns])

        asn_id = self.iyp.batch_get_nodes('AS', 'asn', asns)
        sibling_id = self.iyp.batch_get_nodes('AS', 'asn', sibling_asns)
        url_id = self.iyp.batch_get_nodes('URL', 'url', urls)

        asn_to_url_links = []
        asn_to_sibling_asn_links = []

        connections = {}  # connections are used to remember the relationship between the "AS" and its Sibling.

        for (asn, url, siblings) in lines:
            asn_qid = asn_id[asn]
            url_qid = url_id[url]
            if len(url) > 1:
                asn_to_url_links.append({'src_id': asn_qid, 'dst_id': url_qid, 'props': [self.reference]})
            for sibling in siblings:
                sibling_qid = sibling_id[sibling]
                if asn_qid != sibling_qid:
                    # A check whether asn and sibling are connected already.
                    if asn in connections:
                        if sibling in connections[asn]:
                            continue
                        else:
                            connections[asn].append(sibling)
                            asn_to_sibling_asn_links.append(
                                {'src_id': asn_qid, 'dst_id': sibling_qid, 'props': [self.reference]})
                    else:
                        if sibling in connections:
                            if asn in connections[sibling]:
                                continue
                            else:
                                connections[sibling].append(asn)
                                asn_to_sibling_asn_links.append(
                                    {'src_id': asn_qid, 'dst_id': sibling_qid, 'props': [self.reference]})
                        else:
                            connections[asn] = [sibling]
                            asn_to_sibling_asn_links.append(
                                {'src_id': asn_qid, 'dst_id': sibling_qid, 'props': [self.reference]})

        # Push all links to IYP
        try:
            self.iyp.batch_add_links('WEBSITE', asn_to_url_links)
        except neo4j.exceptions.Neo4jError as e:
            logging.error(e)

        try:
            self.iyp.batch_add_links('SIBLING_OF', asn_to_sibling_asn_links)
        except neo4j.exceptions.Neo4jError as e:
            logging.error(e)


# Main program
if __name__ == '__main__':
    scriptname = sys.argv[0].replace('/', '_')[0:-3]
    FORMAT = '%(asctime)s %(processName)s %(message)s'
    logging.basicConfig(
        format=FORMAT,
        filename='log/' + scriptname + '.log',
        level=logging.WARNING,
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("Started: %s" % sys.argv)

    siblings_asdb = Crawler(ORG, URL, NAME)
    if len(sys.argv) == 2 and sys.argv[1] == 'unit_test':
        siblings_asdb.unit_test(logging)
    else:
        siblings_asdb.run()
        siblings_asdb.close()

    logging.info("End: %s" % sys.argv)
