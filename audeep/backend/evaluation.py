# Copyright (C) 2017  Michael Freitag, Shahin Amiriparian, Sergey Pugachevskiy, Nicholas Cummins, Björn Schuller
#
# This file is part of auDeep.
#
# auDeep is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# auDeep is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with auDeep.  If not, see <http://www.gnu.org/licenses/>.

"""Cross-validated or partitioned evaluation of a learner on some data"""
from typing import Sequence

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, recall_score
from sklearn.preprocessing import StandardScaler

from audeep.backend.data.data_set import DataSet, Split, Partition
from audeep.backend.data.upsample import upsample
from audeep.backend.learners import LearnerBase
from audeep.backend.log import LoggingMixin


def _majority_vote(chunked_data_set: DataSet,
                   chunked_predictions: np.ndarray) -> (np.ndarray, np.ndarray):
    """
    Compute predictions and true labels on a chunked data set using majority voting.
    
    In a valid data set, all chunks of the same audio file have the same labels, so there is no ambiguity in computing
    the true labels of audio files. Predictions for an audio file are computed by looking at the predictions for the
    individual chunks, and selecting the most-chosen prediction.
    
    Parameters
    ----------
    chunked_data_set: DataSet
        A data set containing true labels of chunked audio files
    chunked_predictions: numpy.ndarray
        A one-dimensional NumPy array containing predictions for the chunks. Must have the same number of entries as 
        the specified data set has instances.
        
    Returns
    -------
    true_labels: numpy.ndarray
        The true labels of the audio files in the specified data set.
    predictions: numpy.ndarray
        The predictions for the audio files computed using majority voting over the predictions on the individual chunks
    """
    true_labels = {}
    predictions = {}

    for index in chunked_data_set:
        instance = chunked_data_set[index]

        true_labels[instance.filename] = instance.label_numeric

        if instance.filename not in predictions:
            predictions[instance.filename] = []

        predictions[instance.filename].append(chunked_predictions[index])

    true_labels = [item[1] for item in sorted(true_labels.items(), key=lambda item: item[0])]
    predictions = [np.argmax(np.bincount(item[1])) for item in sorted(predictions.items(), key=lambda item: item[0])]

    return np.array(true_labels), np.array(predictions)


def uar_score(labels: np.ndarray,
              predictions: np.ndarray):
    """
    Computes the unweighted average recall for the specified true labels and predictions.
    
    The unweighted average recall is simply the average recall for each class without any weighting.
    
    Parameters
    ----------
    labels: numpy.ndarray
        A one-dimensional numpy array containing the true labels of instances
    predictions
        A one-dimensional numpy array containing the predicted labels of instances

    Returns
    -------
    float
        The unweighted average recall for the specified true labels and predictions
    """
    return recall_score(labels, predictions, average="macro")


class CrossValidatedEvaluation(LoggingMixin):
    """
    Cross-validated evaluation of a learner on some data.
    
    Given a data set and a learner, this class computes cross-validated accuracy and unweighted average recall, and a
    95% confidence interval for both values. Additionally, a confusion matrix is summed over all cross-validation folds.
    """

    def __init__(self,
                 learner: LearnerBase,
                 upsample: bool,
                 majority_vote: bool):
        """
        Creates and initializes a new cross-validated evaluation of the specified learner.
        
        The `run` method has to be invoked on some data before results can be retrieved.
        
        Parameters
        ----------
        learner: LearnerBase
            A learner which should be evaluated
        upsample: bool
            Balance classes in the training splits of each fold by upsampling instances
        """
        super().__init__()

        self._learner = learner
        self._upsample = upsample
        self._majority_vote = majority_vote
        self._accuracy = None
        self._accuracy_confidence_interval = None
        self._uar = None
        self._uar_confidence_interval = None
        self._confusion_matrix = None

    @property
    def accuracy(self) -> float:
        """
        Returns the accuracy of the learner.
        
        This property returns the accuracy on the last data set on which the `run` method has been invoked. As a 
        consequence, this property returns None if the `run` method has not yet been invoked.
        
        Returns
        -------
        float
            Returns the accuracy of the learner on the last data set passed to the `run` method
        """
        return self._accuracy

    @property
    def accuracy_confidence_interval(self) -> float:
        """
        Returns a 95% confidence interval for the accuracy of the learner.
        
        This property returns the confidence interval on the last data set on which the `run` method has been invoked. 
        As a consequence, this property returns None if the `run` method has not yet been invoked.
        
        Returns
        -------
        float
            Returns a 95% confidence interval for the accuracy of the learner on the last data set passed to the `run` 
            method
        """
        return self._accuracy_confidence_interval

    @property
    def uar(self) -> float:
        """
        Returns the unweighted average recall of the learner.
        
        This property returns the unweighted average recall on the last data set on which the `run` method has been 
        invoked. As a consequence, this property returns None if the `run` method has not yet been invoked.
        
        Returns
        -------
        float
            Returns the unweighted average recall of the learner on the last data set passed to the `run` method
        """
        return self._uar

    @property
    def uar_confidence_interval(self) -> float:
        """
        Returns a 95% confidence interval for the unweighted average recall of the learner.
        
        This property returns the confidence interval on the last data set on which the `run` method has been invoked. 
        As a consequence, this property returns None if the `run` method has not yet been invoked.
        
        Returns
        -------
        float
            Returns a 95% confidence interval for the unweighted average recall of the learner on the last data set 
            passed to the `run` method
        """
        return self._uar_confidence_interval

    @property
    def confusion_matrix(self) -> np.ndarray:
        """
        Returns the confusion matrix of the learner.
        
        This property returns the confusion matrix on the last data set on which the `run` method has been invoked. 
        As a consequence, this property returns None if the `run` method has not yet been invoked. The confusion matrix
        is computed as the sum of the confusion matrices on the individual cross-validation folds.
        
        Returns
        -------
        numpy.ndarray
            The confusion matrix of the learner on the last data set passed to the `run` method
        """
        return self._confusion_matrix

    def run(self,
            data_set: DataSet):
        """
        Evaluates the learner on the specified data set using cross-validation.
        
        Sets the various properties of this instance to the values obtained during evaluation on the specified data set.
        
        Parameters
        ----------
        data_set: DataSet
            The data set on which the learner should be evaluated

        Raises
        ------
        ValueError 
            If the specified data set does not have cross-validation information
        """
        if not data_set.has_cv_info:
            raise ValueError("data set does not have cross validation info")

        accuracies = []
        uars = []
        confusion_matrices = []

        # order numeric labels by nominal value
        ordered_labels = sorted(data_set.label_map.items(), key=lambda t: t[0])
        ordered_labels = list(zip(*ordered_labels))[1]

        for fold in range(data_set.num_folds):
            self.log.info("processing cross validation fold %d...", fold + 1)

            train_split = data_set.split(fold=fold,
                                         split=Split.TRAIN)

            if self._upsample:
                train_split = upsample(train_split)

            train_split = train_split.shuffled()
            valid_split = data_set.split(fold=fold,
                                         split=Split.VALID).shuffled()

            scaler = StandardScaler()
            scaler.fit(train_split.features)

            train_split = train_split.scaled(scaler)
            valid_split = valid_split.scaled(scaler)

            self._learner.fit(train_split.features, train_split.labels_numeric)
            chunked_predictions = self._learner.predict(valid_split.features)

            if self._majority_vote:
                true_labels, predictions = _majority_vote(valid_split, chunked_predictions)
            else:
                true_labels = valid_split.labels_numeric
                predictions = chunked_predictions

            accuracy = accuracy_score(true_labels, predictions)
            uar = uar_score(true_labels, predictions)

            accuracies.append(accuracy)
            uars.append(uar)
            confusion_matrices.append(confusion_matrix(y_true=true_labels,
                                                       y_pred=predictions,
                                                       labels=ordered_labels))

            self.log.info("fold %d accuracy is %2.2f%% (UAR %2.2f%%)", fold + 1, 100 * accuracy, 100 * uar)

        self._accuracy = np.mean(accuracies)
        self._accuracy_confidence_interval = 2 * np.std(accuracies)
        self._uar = np.mean(uars)
        self._uar_confidence_interval = 2 * np.std(uars)
        self._confusion_matrix = np.sum(confusion_matrices, axis=0)


class PartitionedEvaluation(LoggingMixin):
    """
    Partitioned evaluation of a learner on some data set.
    
    The learner is trained on specific partitions of the data set, and evaluated on some other specific partitions of
    the data set. Typically, a the learner is tuned by training on the train partition and evaluating on the development
    partition of a data set. Once tuning is complete, the learner is trained on the train and development partitions, 
    and evaluated on the test partition.
    """

    def __init__(self,
                 learner: LearnerBase,
                 train_partitions: Sequence[Partition],
                 eval_partitions: Sequence[Partition],
                 upsample: bool,
                 majority_vote: bool):
        """
        Create a new partitioned evaluation of the specified learner.
         
        Evaluation is performed by training the learner on the specified training partitions, and evaluating the 
        learner on the specified evaluation partitions.
        
        Parameters
        ----------
        learner: LearnerBase
            The learner which should be evaluated
        train_partitions: list of Partition
            The partitions on which the learner should be trained
        eval_partitions: list of Partition
            The partitions on which the learner should be evaluted
        upsample: bool
            Balance classes in the training partitions by upsampling instances
        """
        super().__init__()

        self._learner = learner
        self._train_partitions = train_partitions
        self._eval_partitions = eval_partitions
        self._upsample = upsample
        self._majority_vote = majority_vote
        self._accuracy = None
        self._uar = None
        self._confusion_matrix = None

    @property
    def accuracy(self) -> float:
        """
        Returns the accuracy of the learner.
        
        This property returns the accuracy on the last data set on which the `run` method has been invoked. As a 
        consequence, this property returns None if the `run` method has not yet been invoked.
        
        Returns
        -------
        float
            Returns the accuracy of the learner on the last data set passed to the `run` method
        """
        return self._accuracy

    @property
    def uar(self) -> float:
        """
        Returns the unweighted average recall of the learner.
        
        This property returns the unweighted average recall on the last data set on which the `run` method has been 
        invoked. As a consequence, this property returns None if the `run` method has not yet been invoked.
        
        Returns
        -------
        float
            Returns the unweighted average recall of the learner on the last data set passed to the `run` method
        """
        return self._uar

    @property
    def confusion_matrix(self) -> np.ndarray:
        """
        Returns the confusion matrix of the learner.
        
        This property returns the confusion matrix on the last data set on which the `run` method has been invoked. 
        As a consequence, this property returns None if the `run` method has not yet been invoked.
        
        Returns
        -------
        numpy.ndarray
            The confusion matrix of the learner on the last data set passed to the `run` method
        """
        return self._confusion_matrix

    def run(self,
            data_set: DataSet):
        """
        Evaluates the learner on the specified data set.
        
        Sets the various properties of this instance to the values obtained during evaluation on the specified data set.
        
        Parameters
        ----------
        data_set: DataSet
            The data set on which the learner should be evaluated

        Raises
        ------
        ValueError 
            If the specified data set does not have partition information
        """
        if not data_set.has_partition_info:
            raise ValueError("data set does not have partition info")

        self.log.info("preparing data set")

        train_split = data_set.partitions(self._train_partitions)

        if self._upsample:
            train_split = upsample(train_split)

        train_split = train_split.shuffled()
        eval_split = data_set.partitions(self._eval_partitions).shuffled()

        scaler = StandardScaler()
        scaler.fit(train_split.features)

        train_split = train_split.scaled(scaler)
        eval_split = eval_split.scaled(scaler)

        self.log.info("training classifier")

        self._learner.fit(train_split.features, train_split.labels_numeric)
        chunked_predictions = self._learner.predict(eval_split.features)

        if self._majority_vote:
            true_labels, predictions = _majority_vote(eval_split, chunked_predictions)
        else:
            true_labels = eval_split.labels_numeric
            predictions = chunked_predictions

        self._accuracy = accuracy_score(true_labels, predictions)
        self._uar = uar_score(true_labels, predictions)

        # order numeric labels by nominal value
        ordered_labels = sorted(data_set.label_map.items(), key=lambda t: t[0])
        ordered_labels = list(zip(*ordered_labels))[1]

        self._confusion_matrix = confusion_matrix(y_true=true_labels,
                                                  y_pred=predictions,
                                                  labels=ordered_labels)
