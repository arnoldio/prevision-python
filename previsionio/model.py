# -*- coding: utf-8 -*-
from __future__ import print_function
import time
import json
import uuid
import requests
import pandas as pd
import previsionio as pio
from functools import lru_cache

from .logger import logger
from .dataset import Dataset
from .deployed_model import DeployedModel
from .prevision_client import client, EventManager
from .api_resource import ApiResource
from .utils import NpEncoder, parse_json, EventTuple, \
    PrevisionException, zip_to_pandas


class Model(ApiResource):
    """ A Model object is generated by Prevision AutoML plateform when you launch a use case.
    All models generated by Prevision.io are deployable in our Store

    With this Model class, you can select the model with the optimal hyperparameters
    that responds to your buisiness requirements, then you can deploy it
    as a real-time/batch endpoint that can be used for a web Service.

    Args:
        _id (str): Unique id of the model
        uc_id (str): Unique id of the usecase of the model
        uc_version (str, int): Version of the usecase of the model (either an integer for a specific
            version, or "last")
        name (str, optional): Name of the model (default: ``None``)
    """

    def __init__(self, _id, uc_id, uc_version, name=None, **other_params):
        """ Instantiate a new :class:`.Model` object to manipulate a model resource on the platform. """
        super().__init__()
        self._id = _id
        self.uc_id = uc_id
        self.uc_version = uc_version
        self.name = name

        for k, v in other_params.items():
            self.__setattr__(k, v)

        event_url = '{}/usecases/{}/versions/{}/predictions/events'.format(pio.client.url,
                                                                           self.uc_id,
                                                                           self.uc_version)
        self.prediction_event_manager = EventManager(event_url,
                                                     auth_headers=pio.client.headers)

    def __repr__(self):
        return str(self._id)

    def __str__(self):
        """ Show up the Model object attributes.

        Returns:
            str: JSON-formatted info
        """
        args_to_show = {k: self.__dict__[k]
                        for k in self.__dict__
                        if all(map(lambda x: x not in k.lower(),
                                   ["event", "compositiondetails"])
                               )
                        }

        return json.dumps(args_to_show, sort_keys=True, indent=4, separators=(',', ': '))

    @property
    @lru_cache()
    def hyperparameters(self):
        """ Return the hyperparameters of a model.

        Returns:
            dict: Hyperparameters of the model
        """
        response = client.request(
            endpoint='/usecases/{}/versions/{}/models/{}/download/hyperparameters'.format(self.uc_id,
                                                                                          self.uc_version,
                                                                                          self._id),
            method=requests.get)
        return (json.loads(response.content.decode('utf-8')))

    @property
    @lru_cache()
    def feature_importance(self) -> pd.DataFrame:
        """ Return a dataframe of feature importances for the given model features, with their corresponding
        scores (sorted by descending feature importance scores).

        Returns:
            ``pd.DataFrame``: Dataframe of feature importances

        Raises:
            PrevisionException: Any error while fetching data from the platform or parsing the result
        """
        response = client.request(
            endpoint='/usecases/{}/versions/{}/models/{}/download/features-importance'.format(self.uc_id,
                                                                                              self.uc_version,
                                                                                              self._id),
            method=requests.get)
        if response.ok:
            df_feat_importance = zip_to_pandas(response)
        else:
            raise PrevisionException(
                'Failed to download feature importance table: {}'.format(response.text))

        return df_feat_importance.sort_values(by="importance", ascending=False)

    def chart(self):
        """ Return chart analysis information for a model.

        Returns:
            dict: Chart analysis results

        Raises:
            PrevisionException: Any error while fetching data from the platform or parsing the result
        """
        response = client.request(
            endpoint='/usecases/{}/versions/{}/models/{}/analysis'.format(self.uc_id, self.uc_version, self._id),
            method=requests.get)
        result = (json.loads(response.content.decode('utf-8')))
        if result.get('status', 200) != 200:
            msg = result['message']
            logger.error(msg)
            raise PrevisionException(msg)
        # drop chart-related information
        return result

    def _get_uc_info(self):
        """ Return the corresponding usecase summary.

        Returns:
            dict: Usecase summary
        """
        response = client.request(endpoint='/usecases/{}/versions/{}'.format(self.uc_id, self.uc_version),
                                  method=requests.get)

        return json.loads(response.content.decode('utf-8'))

    def wait_for_prediction(self, predict_id):
        """ Wait for a specific prediction to finish.

        Args:
            predict_id (str): Unique id of the prediction to wait for
        """
        self.prediction_event_manager.wait_for_event(predict_id,
                                                     'usecases/{}/versions/{}/predictions'.format(self.uc_id,
                                                                                                  self.uc_version),
                                                     EventTuple('status', 'done'))

    def _predict_bulk(self,
                      dataset_id,
                      confidence=False,
                      dataset_folder_id=None):
        """ (Util method) Private method used to handle bulk predict.

        .. note::

            This function should not be used directly. Use predict_from_* methods instead.

        Args:
            dataset_id (str): Unique id of the dataset to predict with
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)
            dataset_folder_id (str, optional): Unique id of the associated folder dataset to predict with,
                if need be (default: ``None``)

        Returns:
            str: A prediction job ID

        Raises:
            PrevisionException: Any error while starting the prediction on the platform or parsing the result
        """
        data = {
            'usecaseId': self.uc_id,
            'datasetId': dataset_id,
            'modelId': self._id,
            'bestSingle': 'false',  # because we"ll be using the current model
            'confidence': str(confidence).lower(),
        }
        if self._id:
            confidence_check = client.request('/usecases/{}/versions/{}/models/{}/confidence'.format(self.uc_id, self.uc_version, self._id),
                                          requests.get)
            confidence_check_parsed = parse_json(confidence_check)
            if confidence_check_parsed and 'confidence' in confidence_check_parsed:
                if confidence_check_parsed['confidence'] == False:
                    data['confidence'] == 'false'
        else:
            data['confidence'] == 'false'

        if dataset_folder_id is not None:
            data['datasetFolderId'] = dataset_folder_id
        predict_start = client.request('/usecases/{}/versions/{}/predictions'.format(self.uc_id, self.uc_version),
                                       requests.post, data=data)

        predict_start_parsed = parse_json(predict_start)

        if '_id' not in predict_start_parsed:
            err = 'Error starting prediction: {}'.format(predict_start_parsed)
            logger.error(err)
            raise PrevisionException(err)

        return predict_start_parsed['_id']

    def predict_single(self, confidence=False, explain=False, **predict_data):
        """ Make a prediction for a single instance. Use :py:func:`predict_from_dataset_name` or predict methods
        to predict multiple instances at the same time (it's faster).

        Args:
            confidence (bool, optional): Whether to predict with confidence values (default: ``False``)
            explain (bool, optional): Whether to explain prediction (default: ``False``)
            **predict_data: Features names and values (without target feature) - missing feature keys
                will be replaced by nans

        .. note::

            You can set both ``confidence`` and ``explain`` to true.

        Returns:
            dict: Dictionary containing the prediction result

            .. note::

                The prediction format depends on the problem type (regression, classification, etc...)
        """
        payload = {
            'features': {
                str(k): v for k, v in predict_data.items() if str(v) != 'nan'
            },
            'explain': explain,
            'confidence': confidence,
            'best': False,
            'specific_model': self._id
        }

        logger.debug('[Predict Unit] sending payload ' + str(payload))

        response = client.request('/usecases/{}/versions/{}/predictions/unit'.format(self.uc_id, self.uc_version),
                                  requests.post,
                                  data=json.dumps(payload, cls=NpEncoder),
                                  content_type='application/json')

        if response.status_code != 200:
            raise PrevisionException('error getting response data: ' + response.text)
        try:
            response_json = parse_json(response)
        except PrevisionException as e:
            logger.error('error getting response data: ' + str(e) + ' -- ' + response.text[0:250])
            raise e
        else:
            if 'prediction' not in response_json:
                raise PrevisionException('error getting response data: ' + response_json.__repr__())
            else:
                return response_json['prediction']

    def _get_predictions(self, predict_id) -> pd.DataFrame:
        """ Get the result prediction dataframe from a given predict id.

        Args:
            predict_id (str): Prediction job ID

        Returns:
            ``pd.DataFrame``: Prediction dataframe.
        """
        pred_response = pio.client.request('/usecases/{}/versions/{}/predictions/{}/download'.format(self.uc_id,
                                                                                                     self.uc_version,
                                                                                                     predict_id),
                                           requests.get)

        logger.debug('[Predict {0}] Downloading prediction file'.format(predict_id))

        return zip_to_pandas(pred_response)

    def _format_predictions(self, preds, confidence=False):
        raise NotImplementedError

    def _predict_sklearn(self, df, confidence):
        """ Make a prediction for a dataframe with a Scikit-learn style blocking prediction mode.

        Args:
            df (``pd.DataFrame``): Dataframe to predict from
            confidence (bool): Whether to predict with confidence estimator

        Returns:
            ``pd.DataFrame``: Predictions result dataframe
        """
        dataset = Dataset.new('test_{}_{}'.format(self.name, str(uuid.uuid4())[-6:]), dataframe=df)

        predict_id = self._predict_bulk(dataset.id,
                                        confidence=confidence)

        self.wait_for_prediction(predict_id)
        dataset.delete()

        return self._get_predictions(predict_id)

    def predict_from_dataset_name(self, dataset_name, confidence=False) -> pd.DataFrame:
        """ Make a prediction for a dataset stored in the current active [client]
        workspace (referenced by name).

        Args:
            dataset_name (str): Name of the dataset to make a prediction for (if there is
                more than one dataset having the given name, the first one will be used)
            confidence (bool, optional): Whether to predict with confidence values (default: ``False``)

        Returns:
            ``pd.DataFrame``: Prediction results dataframe
        """
        dataset_id = Dataset.getid_from_name(name=dataset_name)
        predict_id = self._predict_bulk(dataset_id,
                                        confidence=confidence)

        self.wait_for_prediction(predict_id)

        return self._get_predictions(predict_id)

    def predict_from_dataset(self, dataset, confidence=False, dataset_folder=None) -> pd.DataFrame:
        """ Make a prediction for a dataset stored in the current active [client]
        workspace (using the current SDK dataset object).

        Args:
            dataset (:class:`.Dataset`): Dataset resource to make a prediction for
            confidence (bool, optional): Whether to predict with confidence values (default: ``False``)
            dataset_folder (:class:`.Dataset`, None): Matching folder dataset resource for the prediction,
                if necessary

        Returns:
            ``pd.DataFrame``: Prediction results dataframe
        """
        predict_id = self._predict_bulk(dataset.id,
                                        confidence=confidence,
                                        dataset_folder_id=dataset_folder.id if dataset_folder else None)

        self.wait_for_prediction(predict_id)

        # FIXME : wait_for_prediction() seems to be broken...
        retry_count = 60
        retry = 0
        while retry < retry_count:
            retry += 1
            try:
                preds = self._get_predictions(predict_id)
                return preds
            except Exception:
                # FIXME:
                # sometimes I observed error 500, with prediction on image usecase
                logger.warning('wait_for_prediction has prolly exited {} seconds too early'
                               .format(retry))
                time.sleep(1)
        return None

    def predict(self, df, confidence=False) -> pd.DataFrame:
        """ Make a prediction in a Scikit-learn blocking style.

        .. warning::

            For large dataframes and complex (blend) models, this can be slow (up to 1-2 hours). Prefer using
            this for simple models and small dataframes or use option ``use_best_single = True``.

        Args:
            df (``pd.DataFrame``): A ``pandas`` dataframe containing the testing data
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)

        Returns:
            ``pd.DataFrame``: Prediction results dataframe
        """
        return self._format_predictions(self._predict_sklearn(df, confidence))

    @property
    def cross_validation(self) -> pd.DataFrame:
        """ Get model's cross validation dataframe.

        Returns:
            ``pd.Dataframe``: Cross-validation dataframe
        """
        logger.debug('getting cv, model_id: {}'.format(self.id))
        cv_response = client.request(
            '/usecases/{}/versions/{}/models/{}/download/cv'.format(self.uc_id, self.uc_version, self._id),
            requests.get)

        df_cv = zip_to_pandas(cv_response)

        return df_cv

    def deploy(self) -> DeployedModel:
        """ (Not Implemented yet) Deploy the model as a REST API app.

        Keyword Arguments:
            app_type {enum} -- it can be 'model', 'notebook', 'shiny', 'dash' or 'node' application

        Returns:
            str: Path of the deployed application
        """
        raise NotImplementedError


class ClassificationModel(Model):
    """ A model object for a (binary) classification usecase, i.e. a usecase where the target
    is categorical with exactly 2 modalities.

    Args:
        _id (str): Unique id of the model
        uc_id (str): Unique id of the usecase of the model
        uc_version (str, int): Version of the usecase of the model (either an integer for a specific
            version, or "last")
        name (str, optional): Name of the model (default: ``None``)
    """

    def __init__(self, _id, uc_id, name=None, **other_params):
        """ Instantiate a new :class:`.ClassificationModel` object to manipulate a classification model
        resource on the platform. """
        super().__init__(_id, uc_id, name=name, **other_params)
        self._predict_threshold = 0.5

    def _format_predictions(self, preds, confidence=False, apply_threshold=True):
        """ Format predictions dataframe. In particular, you can apply a threshold on the predictions
        probability.

        Args:
            preds (``pd.DataFrame``): Predictions dataframe
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)
            apply_threshold (bool, optional): Whether to apply a threshold on the predicted probabilities
                (default: ``True``)

        Returns:
            ``pd.DataFrame``: Formatted predictions dataframe.
        """
        pred_col = preds.columns[-1]
        preds[pred_col] = preds[pred_col].astype(float)

        if apply_threshold:
            preds['predictions'] = (preds[pred_col] > self._predict_threshold)
            preds[pred_col] = preds['predictions'].astype(int)
            preds[pred_col] = preds['predictions'].astype(int)
            preds = preds.drop('predictions', axis=1)

        return preds

    def predict_proba(self, df, confidence=False):
        """ Make a prediction in a Scikit-learn blocking style and return probabilities.

        .. warning::

            For large dataframes and complex (blend) models, this can be slow (up to 1-2 hours). Prefer using
            this for simple models and small dataframes or use option ``use_best_single = True``.

        Args:
            df (``pd.DataFrame``): A ``pandas`` dataframe containing the testing data
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)

        Returns:
            ``pd.DataFrame``: (Formatted) prediction results dataframe
        """
        return self._format_predictions(self._predict_sklearn(df, confidence=confidence),
                                        confidence=confidence, apply_threshold=False)

    def predict_single(self, confidence=False, explain=False, **predict_data):
        """ Make a prediction for a single instance.

        Args:
            confidence (bool, optional): Whether to predict with confidence values (default: ``False``)
            explain (bool, optional): Whether to explain prediction (default: ``False``)
            **predict_data: Features names and values (without target feature) - missing feature keys
                will be replaced by nans

        .. note::

            You can set both ``confidence`` and ``explain`` to true.

        Returns:
            tuple: Predictions probability, predictions class, predictions confidence and predictions explanation
        """
        single_pred = super().predict_single(confidence=confidence, explain=explain, **predict_data)

        pred_index = list(filter(lambda x: 'pred' in x,
                                 single_pred.keys()))[0]
        res = single_pred[pred_index]

        return res, int(res > self._predict_threshold), single_pred.get('confidence'), single_pred.get('explanation')

    @property
    @lru_cache()
    def optimal_threshold(self):
        """ Get the value of threshold probability that optimizes the F1 Score.

        Returns:
            float: Optimal value of the threshold (if it not a classification problem it returns ``None``)

        Raises:
            PrevisionException: Any error while fetching data from the platform or parsing the result
        """
        endpoint = '/usecases/{}/versions/{}/models/{}/analysis/dynamic'.format(self.uc_id, self.uc_version, self._id)
        response = client.request(endpoint=endpoint,
                                  method=requests.get)

        resp = json.loads(response.content.decode('utf-8'))
        if response.ok:
            return resp["optimalProba"]
        raise PrevisionException('Request Error : {}'.format(response.content['message']))

    def get_dynamic_performances(self, threshold=0.5):
        """ Get model performance for the given threshold.

        Args:
            threshold (float, optional): Threshold to check the model's performance for (default: 0.5)

        Returns:
            dict: Model classification performance dict with the following keys:

                - ``confusion_matrix``
                - ``accuracy``
                - ``precision``
                - ``recall``
                - ``f1_score``

        Raises:
            PrevisionException: Any error while fetching data from the platform or parsing the result
        """
        threshold = float(threshold)
        if any((threshold > 1, threshold < 0)):
            err = 'threshold value has to be between 0 and 1'
            logger.error(err)
            raise ValueError(err)

        result = dict()
        query = '?threshold={}'.format(str(threshold))
        endpoint = '/usecases/{}/versions/{}/models/{}/analysis/dynamic{}'.format(self.uc_id, self.uc_version,
                                                                                  self._id, query)

        response = client.request(endpoint=endpoint,
                                  method=requests.get)

        resp = json.loads(response.content.decode('utf-8'))

        if response.ok:
            result['confusion_matrix'] = resp["confusionMatrix"]
            for metric in ['accuracy', 'precision', 'recall', 'f1Score']:
                result[metric] = resp["score"][metric]

            return result
        raise PrevisionException('Request Error : {}'.format(response.content['message']))


class RegressionModel(Model):
    """ A model object for a regression usecase, i.e. a usecase where the target is numerical.

    Args:
        _id (str): Unique id of the model
        uc_id (str): Unique id of the usecase of the model
        uc_version (str, int): Version of the usecase of the model (either an integer for a specific
            version, or "last")
        name (str, optional): Name of the model (default: ``None``)
    """

    def _format_predictions(self, preds, confidence=False, explain=False):
        """ Format predictions dataframe (returns the dataframe as is).

        Args:
            preds (``pd.DataFrame``): Predictions dataframe
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)
            apply_threshold (bool, optional): Whether to apply a threshold on the predicted probabilities
                (default: ``True``)

        Returns:
            ``pd.DataFrame``: Formatted predictions dataframe.
        """
        return preds

    def predict(self, df, confidence=False):
        """ Make a prediction in a Scikit-learn blocking style.

        .. warning::

            For large dataframes and complex (blend) models, this can be slow (up to 1-2 hours). Prefer using
            this for simple models and small dataframes or use option ``use_best_single = True``.

        Args:
            df (``pd.DataFrame``): A ``pandas`` dataframe containing the testing data
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)

        Returns:
            ``pd.DataFrame``: Prediction results dataframe
        """
        return self._predict_sklearn(df, confidence=confidence)


class MultiClassificationModel(Model):
    """ A model object for a multi-classification usecase, i.e. a usecase where the target
    is categorical with strictly more than 2 modalities.

    Args:
        _id (str): Unique id of the model
        uc_id (str): Unique id of the usecase of the model
        uc_version (str, int): Version of the usecase of the model (either an integer for a specific
            version, or "last")
        name (str, optional): Name of the model (default: ``None``)
    """

    def _format_predictions(self, preds, confidence=False, explain=False, apply_threshold=True):
        """ Format predictions dataframe. In particular, you can apply a threshold on the predictions
        probability.

        Args:
            preds (``pd.DataFrame``): Predictions dataframe
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)
            explain (bool, optional): Whether to explain prediction (default: ``False``)
            apply_threshold (bool, optional): Whether to apply a threshold on the predicted probabilities
                (default: ``True``)

        Returns:
            ``pd.DataFrame``: Formatted predictions dataframe.
        """
        # TODO check with web team for a more consistent return format
        if 'pred_' in preds.columns[1]:
            pred_col = preds.columns[1]
        else:
            pred_col = preds.columns[2]

        preds[pred_col] = preds[pred_col].astype(int)

        if apply_threshold:
            return preds[['ID', pred_col]]
        else:
            return preds[['ID'] + [c for c in preds.columns if pred_col + '_' in c]]

    def predict_proba(self, df, confidence=False):
        """ Make a prediction in a Scikit-learn blocking style and return probabilities.

        .. warning::

            For large dataframes and complex (blend) models, this can be slow (up to 1-2 hours). Prefer using
            this for simple models and small dataframes or use option ``use_best_single = True``.

        Args:
            df (``pd.DataFrame``): A ``pandas`` dataframe containing the testing data
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)

        Returns:
            ``pd.DataFrame``: (Formatted) prediction results dataframe
        """
        return self._format_predictions(self._predict_sklearn(df, confidence=confidence),
                                        confidence=confidence,
                                        apply_threshold=False)

    def predict(self, df, confidence=False):
        """ Make a prediction in a Scikit-learn blocking style.

        .. warning::

            For large dataframes and complex (blend) models, this can be slow (up to 1-2 hours). Prefer using
            this for simple models and small dataframes or use option ``use_best_single = True``.

        Args:
            df (``pd.DataFrame``): A ``pandas`` dataframe containing the testing data
            confidence (bool, optional): Whether to predict with confidence estimator (default: ``False``)

        Returns:
            ``pd.DataFrame``: (Formatted) prediction results dataframe
        """
        return self._format_predictions(self._predict_sklearn(df, confidence), confidence,
                                        apply_threshold=True)
