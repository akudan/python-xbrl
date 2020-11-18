#! /usr/bin/env python
# encoding: utf-8

import re
from marshmallow import Schema, fields
import datetime
from collections import OrderedDict
from dataclasses import dataclass
import logging
from io import StringIO


def soup_maker(fh):
    """ Takes a file handler returns BeautifulSoup"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(fh, "lxml")
        for tag in soup.find_all():
            tag.name = tag.name.lower()
    except ImportError:
        from BeautifulSoup import BeautifulStoneSoup
        soup = BeautifulStoneSoup(fh)
    return soup


class XBRLFile:
    def __init__(self, fh):
        """
        fh should be a seekable file-like byte stream object
        """
        self.headers = OrderedDict()
        self.fh = fh


class XBRLException(Exception):
    pass


class XBRL:

    def __init__(self):
        self.context_ids = {}
        self.gaap_data = {}
        self.dei = None
        self.custom_data = None

    def get_GAAP(self, context, end_date=None):

        # the default is today
        if end_date is None:
            end_date = datetime.date.today()
        elif isinstance(end_date, str):
            end_date = datetime.datetime.strptime(end_date, "%Y%m%d")

        # current is the previous quarter
        if context == "quarter":
            context = datetime.timedelta(days=90)

        elif context == "year":
            context = datetime.timedelta(days=360)

        elif context == "instant":
            pass
        
        elif isinstance(context, datetime.timedelta):
            pass

        else:
            try:
                context = datetime.timedelta(days=int(context))
            except (ValueError, TypeError):
                raise ValueError('invalid context')

        # we need start date unless instant
        start_date = None
        if context != "instant":
            start_date = end_date - context

        ctx_id = None
        for cid, ctx_dates in self.context_ids.items():
            if context == "instant" and len(ctx_dates) == 1 and ctx_dates[0] == end_date:
                ctx_id = cid
                break
            elif len(ctx_dates) == 2:
                found_start_date, found_end_date = ctx_dates
                if (context <= (found_end_date - found_start_date) <= (context + datetime.timedelta(weeks=1))) \
                    and (found_end_date == end_date):
                        ctx_id = cid
                        break

        if ctx_id is None:
            raise Exception("no context id matched")

        gaap_obj = GAAP()
        for k, k_data in self.gaap_data.items():
            v = k_data.get(ctx_id, 0.)
            setattr(gaap_obj, k, v)

        return gaap_obj


    def get_quarterlies(self, field_names):
        data = {x: {} for x in field_names}
        ctx_ids = [x for x in self.context_ids.keys() if x.endswith("QTD")]
        quarter_re = re.compile("[0-9]{4}Q[1-4]")
        ctx_dict = OrderedDict([(cid, quarter_re.search(cid).group(0)) for cid in sorted(ctx_ids)])
        for k in field_names:
            for cid, quarter in ctx_dict.items():
                data[k][quarter] = self.gaap_data[k].get(cid)

        return data


    def get_yearlies(self, field_names):
        data = {x: {} for x in field_names}
        ctx_ids = [x for x in self.context_ids.keys() if x.endswith("Q4YTD")]
        year_re = re.compile("[0-9]{4}")
        ctx_dict = OrderedDict([(cid, int(year_re.search(cid).group(0))) for cid in sorted(ctx_ids)])
        for k in field_names:
            for cid, year in ctx_dict.items():
                data[k][year] = self.gaap_data[k].get(cid)

        return data


    @classmethod
    def from_file(cls, file_handle, ignore_errors=0):
        """
        parse is the main entry point for an XBRL. It takes a file
        handle.
        """

        if ignore_errors == 2:
            logging.basicConfig(filename='/tmp/xbrl.log',
                level=logging.ERROR,
                format='%(asctime)s %(levelname)s %(name)s %(message)s')
            logger = logging.getLogger(__name__)
        else:
            logger = None

        xbrl_obj = cls()

        # if no file handle was given create our own
        if not hasattr(file_handle, 'read'):
            file_handler = open(file_handle)
        else:
            file_handler = file_handle

        # Store the headers
        xbrl_file = XBRLPreprocessedFile(file_handler)

        xbrl = soup_maker(xbrl_file.fh)
        file_handler.close()

        xbrl_obj.context_ids = XBRL.parse_contexts(xbrl)
        xbrl_obj.gaap_data = XBRL.parse_GAAP(xbrl, ignore_errors, logger)
        xbrl_obj.dei = XBRL.parse_DEI(xbrl, ignore_errors, logger)
        xbrl_obj.custom_data = XBRL.parse_custom(xbrl, ignore_errors, logger)

        return xbrl_obj

    @staticmethod
    def parse_contexts(xbrl):
        xbrl_base = xbrl.find(name=re.compile("xbrl*:*"))

        if xbrl.find('xbrl') is None and xbrl_base is None:
            raise XBRLException('The xbrl file is empty!')

        # lookahead to see if we need a custom leading element
        lookahead = xbrl.find(name=re.compile("context",
                              re.IGNORECASE | re.MULTILINE)).name
        if ":" in lookahead:
            xbrl_base = lookahead.split(":")[0] + ":"
        else:
            xbrl_base = ""
        
        doc_root = ""

        # we might need to attach the document root
        if len(xbrl_base) > 1:
            doc_root = xbrl_base

        # collect all contexts up that are relevant to us
        # TODO - Maybe move this to Preprocessing Ingestion
        context_ids = {}
        context_tags = xbrl.find_all(name=re.compile(doc_root + "context",
                                     re.IGNORECASE | re.MULTILINE))

        try:
            for context_tag in context_tags:
                # we don't want any segments
                if context_tag.find(doc_root + "entity") is None:
                    continue
                if context_tag.find(doc_root + "entity").find(
                doc_root + "segment") is None:
                    context_id = context_tag.attrs['id']

                    found_start_date = None
                    found_end_date = None

                    if context_tag.find(doc_root + "instant"):
                        instant = \
                            datetime.datetime.strptime(re.compile('[^\d]+')
                                                       .sub('', context_tag
                                                       .find(doc_root +
                                                             "instant")
                                                        .text)[:8], "%Y%m%d")
                        context_ids[context_id] = (instant,)
                        continue

                    if context_tag.find(doc_root + "period").find(
                    doc_root + "startdate"):
                        found_start_date = \
                            datetime.datetime.strptime(re.compile('[^\d]+')
                                                       .sub('', context_tag
                                                       .find(doc_root +
                                                             "period")
                                                       .find(doc_root +
                                                             "startdate")
                                                        .text)[:8], "%Y%m%d")
                    if context_tag.find(doc_root + "period").find(doc_root +
                    "enddate"):
                        found_end_date = \
                            datetime.datetime.strptime(re.compile('[^\d]+')
                                                       .sub('', context_tag
                                                       .find(doc_root +
                                                             "period")
                                                       .find(doc_root +
                                                             "enddate")
                                                       .text)[:8], "%Y%m%d")
                    if found_end_date and found_start_date:
                        context_ids[context_id] = (found_start_date, found_end_date)
        except IndexError:
            raise XBRLException('problem getting contexts')

        return context_ids

    @staticmethod
    def parse_GAAP(xbrl,
                  ignore_errors,
                  logger):
        """
        Parse GAAP from our XBRL soup
        """

        gaap_data = {}

        assets = xbrl.find_all("us-gaap:assets")
        gaap_data["assets"] = XBRL.data_processing(assets, ignore_errors, logger)

        current_assets = \
            xbrl.find_all("us-gaap:assetscurrent")
        gaap_data["current_assets"] = XBRL.data_processing(current_assets, ignore_errors, logger)

        non_current_assets = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(assetsnoncurrent)",
                          re.IGNORECASE | re.MULTILINE))
        if non_current_assets == 0 or not non_current_assets:
            # Assets  = AssetsCurrent  +  AssetsNoncurrent
            gaap_data["non_current_assets"] = gaap_data["assets"] \
                - gaap_data["current_assets"]
        else:
            gaap_data["non_current_assets"] = \
                XBRL.data_processing(non_current_assets, ignore_errors, logger)

        liabilities_and_equity = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(liabilitiesand)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["liabilities_and_equity"] = \
            XBRL.data_processing(liabilities_and_equity, ignore_errors, logger)

        liabilities = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(liabilities)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["liabilities"] = \
            XBRL.data_processing(liabilities, ignore_errors, logger)

        current_liabilities = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]\
                          *(currentliabilities)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["current_liabilities"] = \
            XBRL.data_processing(current_liabilities, ignore_errors, logger)

        noncurrent_liabilities = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]\
                          *(noncurrentliabilities)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["noncurrent_liabilities"] = \
            XBRL.data_processing(noncurrent_liabilities, ignore_errors, logger)

        commitments_and_contingencies = \
            xbrl.find_all(name=re.compile("(us-gaap:commitments\
                          andcontingencies)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["commitments_and_contingencies"] = \
            XBRL.data_processing(commitments_and_contingencies, ignore_errors, logger)

        redeemable_noncontrolling_interest = \
            xbrl.find_all(name=re.compile("(us-gaap:redeemablenoncontrolling\
                          interestequity)", re.IGNORECASE | re.MULTILINE))
        gaap_data["redeemable_noncontrolling_interest"] = \
            XBRL.data_processing(redeemable_noncontrolling_interest, ignore_errors, logger)

        temporary_equity = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(temporaryequity)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["temporary_equity"] = \
            XBRL.data_processing(temporary_equity, ignore_errors, logger)

        equity = xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(equity)",
                               re.IGNORECASE | re.MULTILINE))
        gaap_data["equity"] = XBRL.data_processing(equity, ignore_errors, logger)

        equity_attributable_interest = \
            xbrl.find_all(name=re.compile("(us-gaap:minorityinterest)",
                          re.IGNORECASE | re.MULTILINE))
        equity_attributable_interest += \
            xbrl.find_all(name=re.compile("(us-gaap:partnerscapitalattributable\
                          tononcontrollinginterest)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["equity_attributable_interest"] = \
            XBRL.data_processing(equity_attributable_interest, ignore_errors, logger)

        equity_attributable_parent = \
            xbrl.find_all(name=re.compile("(us-gaap:liabilitiesandpartners\
                          capital)",
                          re.IGNORECASE | re.MULTILINE))
        stockholders_equity = \
            xbrl.find_all(name=re.compile("(us-gaap:stockholdersequity)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["equity_attributable_parent"] = \
            XBRL.data_processing(equity_attributable_parent, ignore_errors, logger)
        gaap_data["stockholders_equity"] = \
            XBRL.data_processing(stockholders_equity, ignore_errors, logger)

        # Incomes #
        revenues = xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(revenues)",
                                 re.IGNORECASE | re.MULTILINE))
        gaap_data["revenues"] = XBRL.data_processing(revenues, ignore_errors, logger)

        cost_of_revenue = \
            xbrl.find_all(name=re.compile("(us-gaap:costofrevenue)",
                          re.IGNORECASE | re.MULTILINE))
        cost_of_revenue += \
            xbrl.find_all(name=re.compile("(us-gaap:costofservices)",
                          re.IGNORECASE | re.MULTILINE))
        cost_of_revenue += \
            xbrl.find_all(name=re.compile("(us-gaap:costofgoodssold)",
                          re.IGNORECASE | re.MULTILINE))
        cost_of_revenue += \
            xbrl.find_all(name=re.compile("(us-gaap:costofgoodsand\
                          servicessold)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["cost_of_revenue"] = \
            XBRL.data_processing(cost_of_revenue, ignore_errors, logger)

        gross_profit = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(grossprofit)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["gross_profit"] = \
            XBRL.data_processing(gross_profit, ignore_errors, logger)

        operating_expenses = \
            xbrl.find_all(name=re.compile("(us-gaap:operating)[^s]*(expenses)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["operating_expenses"] = \
            XBRL.data_processing(operating_expenses, ignore_errors, logger)

        costs_and_expenses = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(costsandexpenses)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["costs_and_expenses"] = \
            XBRL.data_processing(costs_and_expenses, ignore_errors, logger)

        other_operating_income = \
            xbrl.find_all(name=re.compile("(us-gaap:otheroperatingincome)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["other_operating_income"] = \
            XBRL.data_processing(other_operating_income, ignore_errors, logger)

        operating_income_loss = \
            xbrl.find_all(name=re.compile("(us-gaap:otheroperatingincome)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["operating_income_loss"] = \
            XBRL.data_processing(operating_income_loss, ignore_errors, logger)

        nonoperating_income_loss = \
            xbrl.find_all(name=re.compile("(us-gaap:nonoperatingincomeloss)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["nonoperating_income_loss"] = \
            XBRL.data_processing(nonoperating_income_loss, ignore_errors, logger)

        interest_and_debt_expense = \
            xbrl.find_all(name=re.compile("(us-gaap:interestanddebtexpense)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["interest_and_debt_expense"] = \
            XBRL.data_processing(interest_and_debt_expense, ignore_errors, logger)

        income_before_equity_investments = \
            xbrl.find_all(name=re.compile("(us-gaap:incomelossfromcontinuing"
                                          "operationsbeforeincometaxes"
                                          "minorityinterest)",
                          re.IGNORECASE  | re.MULTILINE))
        gaap_data["income_before_equity_investments"] = \
            XBRL.data_processing(income_before_equity_investments, ignore_errors, logger)

        income_from_equity_investments = \
            xbrl.find_all(name=re.compile("(us-gaap:incomelossfromequity"
                          "methodinvestments)", re.IGNORECASE | re.MULTILINE))
        gaap_data["income_from_equity_investments"] = \
            XBRL.data_processing(income_from_equity_investments, ignore_errors, logger)

        income_tax_expense_benefit = \
            xbrl.find_all(name=re.compile("(us-gaap:incometaxexpensebenefit)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["income_tax_expense_benefit"] = \
            XBRL.data_processing(income_tax_expense_benefit, ignore_errors, logger)

        income_continuing_operations_tax = \
            xbrl.find_all(name=re.compile("(us-gaap:IncomeLossBeforeExtraordinaryItems\
                          AndCumulativeEffectOfChangeInAccountingPrinciple)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["income_continuing_operations_tax"] = \
            XBRL.data_processing(income_continuing_operations_tax, ignore_errors, logger)

        income_discontinued_operations = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(discontinued"
                          "operation)", re.IGNORECASE | re.MULTILINE))
        gaap_data["income_discontinued_operations"] = \
            XBRL.data_processing(income_discontinued_operations, ignore_errors, logger)

        extraordary_items_gain_loss = \
            xbrl.find_all(name=re.compile("(us-gaap:extraordinaryitem"
                          "netoftax)", re.IGNORECASE | re.MULTILINE))
        gaap_data["extraordary_items_gain_loss"] = \
            XBRL.data_processing(extraordary_items_gain_loss, ignore_errors, logger)

        income_loss = \
            xbrl.find_all(name=re.compile("(us-gaap:)[^s]*(incomeloss)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["income_loss"] = \
            XBRL.data_processing(income_loss, ignore_errors, logger)
        income_loss += xbrl.find_all(name=re.compile("(us-gaap:profitloss)",
                                     re.IGNORECASE | re.MULTILINE))
        gaap_data["income_loss"] = \
            XBRL.data_processing(income_loss, ignore_errors, logger)

        net_income_shareholders = \
            xbrl.find_all(name=re.compile("(us-gaap:netincomeavailabletocommon\
                          stockholdersbasic)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_income_shareholders"] = \
            XBRL.data_processing(net_income_shareholders, ignore_errors, logger)

        preferred_stock_dividends = \
            xbrl.find_all(name=re.compile("(us-gaap:preferredstockdividendsand\
                          otheradjustments)", re.IGNORECASE | re.MULTILINE))
        gaap_data["preferred_stock_dividends"] = \
            XBRL.data_processing(preferred_stock_dividends, ignore_errors, logger)

        net_income_loss_noncontrolling = \
            xbrl.find_all(name=re.compile("(us-gaap:netincomelossattributableto\
                          noncontrollinginterest)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_income_loss_noncontrolling"] = \
            XBRL.data_processing(net_income_loss_noncontrolling, ignore_errors, logger)

        net_income_loss = \
            xbrl.find_all(name=re.compile("^us-gaap:netincomeloss$",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_income_loss"] = \
            XBRL.data_processing(net_income_loss, ignore_errors, logger)

        other_comprehensive_income = \
            xbrl.find_all(name=re.compile("(us-gaap:othercomprehensiveincomeloss\
                          netoftax)", re.IGNORECASE | re.MULTILINE))
        gaap_data["other_comprehensive_income"] = \
            XBRL.data_processing(other_comprehensive_income, ignore_errors, logger)

        comprehensive_income = \
            xbrl.find_all(name=re.compile("(us-gaap:comprehensiveincome)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["comprehensive_income"] = \
            XBRL.data_processing(comprehensive_income, ignore_errors, logger)

        comprehensive_income_parent = \
            xbrl.find_all(name=re.compile("(us-gaap:comprehensiveincomenetof"
                          "tax)", re.IGNORECASE | re.MULTILINE))
        gaap_data["comprehensive_income_parent"] = \
            XBRL.data_processing(comprehensive_income_parent, ignore_errors, logger)

        comprehensive_income_interest = \
            xbrl.find_all(name=re.compile("(us-gaap:comprehensiveincomenetoftax\
                          attributabletononcontrollinginterest)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["comprehensive_income_interest"] = \
            XBRL.data_processing(comprehensive_income_interest, ignore_errors, logger)

        # Cash flow statements #
        net_cash_flows_operating = \
            xbrl.find_all(name=re.compile("(us-gaap:netcashprovidedbyusedin\
                          operatingactivities)", re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_operating"] = \
            XBRL.data_processing(net_cash_flows_operating, ignore_errors, logger)

        net_cash_flows_investing = \
            xbrl.find_all(name=re.compile("(us-gaap:netcashprovidedbyusedin\
                          investingactivities)", re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_investing"] = \
            XBRL.data_processing(net_cash_flows_investing, ignore_errors, logger)

        net_cash_flows_financing = \
            xbrl.find_all(name=re.compile("(us-gaap:netcashprovidedbyusedin\
                          financingactivities)", re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_financing"] = \
            XBRL.data_processing(net_cash_flows_financing, ignore_errors, logger)

        net_cash_flows_operating_continuing = \
            xbrl.find_all(name=re.compile("(us-gaap:netcashprovidedbyusedin\
                          operatingactivitiescontinuingoperations)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_operating_continuing"] = \
            XBRL.data_processing(net_cash_flows_operating_continuing, ignore_errors, logger)

        net_cash_flows_investing_continuing = \
            xbrl.find_all(name=re.compile("(us-gaap:netcashprovidedbyusedin\
                          investingactivitiescontinuingoperations)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_investing_continuing"] = \
            XBRL.data_processing(net_cash_flows_investing_continuing, ignore_errors, logger)

        net_cash_flows_financing_continuing = \
            xbrl.find_all(name=re.compile("(us-gaap:netcashprovidedbyusedin\
                          financingactivitiescontinuingoperations)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_financing_continuing"] = \
            XBRL.data_processing(net_cash_flows_financing_continuing, ignore_errors, logger)

        net_cash_flows_operating_discontinued = \
            xbrl.find_all(name=re.compile("(us-gaap:cashprovidedbyusedin\
                          operatingactivitiesdiscontinuedoperations)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_operating_discontinued"] = \
            XBRL.data_processing(net_cash_flows_operating_discontinued, ignore_errors, logger)

        net_cash_flows_investing_discontinued = \
            xbrl.find_all(name=re.compile("(us-gaap:cashprovidedbyusedin\
                          investingactivitiesdiscontinuedoperations)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_investing_discontinued"] = \
            XBRL.data_processing(net_cash_flows_investing_discontinued, ignore_errors, logger)

        net_cash_flows_discontinued = \
            xbrl.find_all(name=re.compile("(us-gaap:netcashprovidedbyusedin\
                          discontinuedoperations)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["net_cash_flows_discontinued"] = \
            XBRL.data_processing(net_cash_flows_discontinued, ignore_errors, logger)

        common_shares_outstanding = \
            xbrl.find_all(name=re.compile("(us-gaap:commonstockshares\
                          outstanding)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["common_shares_outstanding"] = \
            XBRL.data_processing(common_shares_outstanding, ignore_errors, logger)

        common_shares_issued = \
            xbrl.find_all(name=re.compile("(us-gaap:commonstockshares\
                          issued)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["common_shares_issued"] = \
            XBRL.data_processing(common_shares_issued, ignore_errors, logger)

        common_shares_authorized = \
            xbrl.find_all(name=re.compile("(us-gaap:commonstockshares\
                          authorized)",
                          re.IGNORECASE | re.MULTILINE))
        gaap_data["common_shares_authorized"] = \
            XBRL.data_processing(common_shares_authorized, ignore_errors, logger)

        return gaap_data

    @staticmethod
    def parse_DEI(xbrl,
                 ignore_errors,
                 logger):
        """
        Parse DEI from our XBRL soup and return a DEI object.
        """
        dei_obj = DEI()

        trading_symbol = xbrl.find_all(name=re.compile("(dei:tradingsymbol)",
            re.IGNORECASE | re.MULTILINE))
        dei_obj.trading_symbol = \
            XBRL.data_processing(trading_symbol,
                                 ignore_errors, logger,
                                 options={'type': 'String',
                                          'no_context': True})

        company_name = xbrl.find_all(name=re.compile("(dei:entityregistrantname)",
            re.IGNORECASE | re.MULTILINE))
        dei_obj.company_name = \
            XBRL.data_processing(company_name,
                                 ignore_errors, logger,
                                 options={'type': 'String',
                                          'no_context': True})

        shares_outstanding = xbrl.find_all(name=re.compile("(dei:entitycommonstocksharesoutstanding)",
            re.IGNORECASE | re.MULTILINE))
        dei_obj.shares_outstanding = \
            XBRL.data_processing(shares_outstanding,
                                 ignore_errors, logger,
                                 options={'type': 'Number',
                                          'no_context': True})

        public_float = xbrl.find_all(name=re.compile("(dei:entitypublicfloat)",
            re.IGNORECASE | re.MULTILINE))
        dei_obj.public_float = \
            XBRL.data_processing(public_float,
                                 ignore_errors, logger,
                                 options={'type': 'Number',
                                          'no_context': True})

        return dei_obj

    @staticmethod
    def parse_custom(xbrl,
                    ignore_errors,
                    logger):
        """
        Parse company custom entities from XBRL and return an Custom object.
        """
        custom_obj = Custom()

        custom_data = xbrl.find_all(re.compile('^((?!(us-gaap|dei|xbrll|xbrldi)).)*:\s*',
            re.IGNORECASE | re.MULTILINE))

        elements = {}
        for data in custom_data:
            if XBRL.is_number(data.text):
                setattr(custom_obj, data.name.split(':')[1], data.text)

        return custom_obj

    @staticmethod
    def is_number(s):
        """
        Test if value is numeric
        """
        try:
            s = float(s)
            return True
        except ValueError:
            return False

    @staticmethod
    def data_processing(elements,
                        ignore_errors,
                        logger,
                        **kwargs):
        """
        Process a XBRL tag object and extract the correct value as
        stated by the context.
        """
        options = kwargs.get('options', {'type': 'Number',
                                         'no_context': False})

        if options['type'] == 'String':
            if len(elements) > 0:
                    return elements[0].text

        if options['no_context'] == True:
            if len(elements) > 0 and XBRL.is_number(elements[0].text):
                    return elements[0].text

        data = {}

        for element in elements:
            try:
                ctx = element.attrs['contextref']

                if XBRL.is_number(element.text):
                    attr_precision = 0
                    decimals = element.attrs['decimals']
                    if decimals is not None:
                        attr_precision = int(decimals)

                    val = float(element.text) if attr_precision > 0 else int(element.text)
                    data[ctx] = val

            except Exception as e:
                if ignore_errors == 0:
                    raise XBRLException('value extraction error')
                elif ignore_errors == 2:
                    logger.error(str(e) + " error at " +
                        ''.join(element.text))
        
        return data


# Preprocessing to fix broken XML
# TODO - Run tests to see if other XML processing errors can occur
class XBRLPreprocessedFile(XBRLFile):
    def __init__(self, fh):
        super(XBRLPreprocessedFile, self).__init__(fh)

        if self.fh is None:
            return

        xbrl_string = self.fh.read()

        # find all closing tags as hints
        closing_tags = [t.upper() for t in re.findall(r'(?i)</([a-z0-9_\.]+)>',
                        xbrl_string)]

        # close all tags that don't have closing tags and
        # leave all other data intact
        last_open_tag = None
        tokens = re.split(r'(?i)(</?[a-z0-9_\.]+>)', xbrl_string)
        new_fh = StringIO()
        for idx, token in enumerate(tokens):
            is_closing_tag = token.startswith('</')
            is_processing_tag = token.startswith('<?')
            is_cdata = token.startswith('<!')
            is_tag = token.startswith('<') and not is_cdata
            is_open_tag = is_tag and not is_closing_tag \
                and not is_processing_tag
            if is_tag:
                if last_open_tag is not None:
                    new_fh.write("</%s>" % last_open_tag)
                    last_open_tag = None
            if is_open_tag:
                tag_name = re.findall(r'(?i)<*>', token)[0]
                if tag_name.upper() not in closing_tags:
                    last_open_tag = tag_name
            new_fh.write(token)
        new_fh.seek(0)
        self.fh = new_fh


# Base GAAP object
@dataclass
class GAAP:
    assets : float = 0.0
    current_assets : float = 0.0
    non_current_assets : float = 0.0
    liabilities_and_equity : float = 0.0
    liabilities : float = 0.0
    current_liabilities : float = 0.0
    noncurrent_liabilities : float = 0.0
    commitments_and_contingencies : float = 0.0
    redeemable_noncontrolling_interest : float = 0.0
    temporary_equity : float = 0.0
    equity : float = 0.0
    equity_attributable_interest : float = 0.0
    equity_attributable_parent : float = 0.0
    stockholders_equity : float = 0.0
    revenues : float = 0.0
    cost_of_revenue : float = 0.0
    gross_profit : float = 0.0
    costs_and_expenses : float = 0.0
    other_operating_income : float = 0.0
    operating_income_loss : float = 0.0
    nonoperating_income_loss : float = 0.0
    interest_and_debt_expense : float = 0.0
    income_before_equity_investments : float = 0.0
    income_from_equity_investments : float = 0.0
    income_tax_expense_benefit : float = 0.0
    extraordary_items_gain_loss : float = 0.0
    income_loss : float = 0.0
    net_income_shareholders : float = 0.0
    preferred_stock_dividends : float = 0.0
    net_income_loss_noncontrolling : float = 0.0
    net_income_parent : float = 0.0
    net_income_loss : float = 0.0
    other_comprehensive_income : float = 0.0
    comprehensive_income : float = 0.0
    comprehensive_income_parent : float = 0.0
    comprehensive_income_interest : float = 0.0
    net_cash_flows_operating : float = 0.0
    net_cash_flows_investing : float = 0.0
    net_cash_flows_financing : float = 0.0
    net_cash_flows_operating_continuing : float = 0.0
    net_cash_flows_investing_continuing : float = 0.0
    net_cash_flows_financing_continuing : float = 0.0
    net_cash_flows_operating_discontinued : float = 0.0
    net_cash_flows_investing_discontinued : float = 0.0
    net_cash_flows_discontinued : float = 0.0
    common_shares_outstanding : float = 0.0
    common_shares_issued : float = 0.0
    common_shares_authorized : float = 0.0


class GAAPSerializer(Schema):
    assets = fields.Number()
    current_assets = fields.Number()
    non_current_assets = fields.Number()
    liabilities_and_equity = fields.Number()
    liabilities = fields.Number()
    current_liabilities = fields.Number()
    noncurrent_liabilities = fields.Number()
    commitments_and_contingencies = fields.Number()
    redeemable_noncontrolling_interest = fields.Number()
    temporary_equity = fields.Number()
    equity = fields.Number()
    equity_attributable_interest = fields.Number()
    equity_attributable_parent = fields.Number()
    stockholders_equity = fields.Number()
    revenues = fields.Number()
    cost_of_revenue = fields.Number()
    gross_profit = fields.Number()
    operating_expenses = fields.Number()
    costs_and_expenses = fields.Number()
    other_operating_income = fields.Number()
    operating_income_loss = fields.Number()
    nonoperating_income_loss = fields.Number()
    interest_and_debt_expense = fields.Number()
    income_before_equity_investments = fields.Number()
    income_from_equity_investments = fields.Number()
    income_tax_expense_benefit = fields.Number()
    extraordary_items_gain_loss = fields.Number()
    income_loss = fields.Number()
    net_income_shareholders = fields.Number()
    preferred_stock_dividends = fields.Number()
    net_income_loss_noncontrolling = fields.Number()
    net_income_parent = fields.Number()
    net_income_loss = fields.Number()
    other_comprehensive_income = fields.Number()
    comprehensive_income = fields.Number()
    comprehensive_income_parent = fields.Number()
    comprehensive_income_interest = fields.Number()
    net_cash_flows_operating = fields.Number()
    net_cash_flows_investing = fields.Number()
    net_cash_flows_financing = fields.Number()
    net_cash_flows_operating_continuing = fields.Number()
    net_cash_flows_investing_continuing = fields.Number()
    net_cash_flows_financing_continuing = fields.Number()
    net_cash_flows_operating_discontinued = fields.Number()
    net_cash_flows_investing_discontinued = fields.Number()
    net_cash_flows_discontinued = fields.Number()
    common_shares_outstanding = fields.Number()
    common_shares_issued = fields.Number()
    common_shares_authorized = fields.Number()


# Base DEI object
@dataclass
class DEI:
    trading_symbol : str = ''
    company_name : str = ''
    shares_outstanding : float = 0.0
    public_float: float = 0.0


class DEISerializer(Schema):
    trading_symbol = fields.String()
    company_name = fields.String()
    shares_outstanding = fields.Number()
    public_float = fields.Number()


# Base Custom object
class Custom:

    def __init__(self):
        return None

    def __call__(self):
        return self.__dict__.items()
